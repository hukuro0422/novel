import streamlit as st
import tempfile
import os
import zipfile
import base64
from io import BytesIO

from novel_downloader import create_epub, get_latest_chapter_count
from database import (
    get_user_by_email,
    register_user,
    get_user_novels,
    register_novel,
    update_latest_chapter,
    record_download,
    delete_novel,
    update_cover_image,
    get_cached_chapters,
    upsert_cached_chapters,
)
from streamlit_cookies_manager import EncryptedCookieManager
import warnings
from PIL import Image

# =========================
# キャッシュ関数
# =========================
@st.cache_data(ttl=300)
def cached_get_user_by_email(email):
    return get_user_by_email(email)


@st.cache_data(ttl=60)
def cached_get_user_novels(email):
    return get_user_novels(email)


@st.cache_data(ttl=600)
def cached_get_latest_chapter_count(url):
    return get_latest_chapter_count(url)


def normalize_novel_rows(value):
    """Supabaseや古いキャッシュ由来の作品一覧を辞書のリストへ揃える。"""
    if value is None:
        return []

    if isinstance(value, dict):
        # Supabaseレスポンス風の {"data": [...]} と、単一行の両方に対応する。
        value = value.get("data") if "data" in value else [value]

    if not isinstance(value, (list, tuple)):
        return []

    novels = []
    for row in value:
        if hasattr(row, "model_dump"):
            row = row.model_dump()
        elif hasattr(row, "dict") and callable(row.dict):
            row = row.dict()

        if not isinstance(row, dict):
            continue

        novel = dict(row)
        if not novel.get("id") or not novel.get("url"):
            continue
        novel["title"] = novel.get("title") or "タイトル不明"
        novels.append(novel)

    return novels

warnings.filterwarnings(
    "ignore",
    message="st.cache is deprecated"
)

# 必ず最初
st.set_page_config(
    page_title="Novel Downloader",
    page_icon="📚",
    layout="wide"
)

# Cookie管理
try:
    cookie_password = st.secrets["COOKIE_PASSWORD"]
except Exception:
    cookie_password = "novel-downloader-local-fallback-key-2026"

cookies = EncryptedCookieManager(
    prefix="novel_downloader/",
    password=cookie_password
)

if not cookies.ready():
    st.info("ブラウザ設定を読み込み中です...")
    st.stop()
    
def process_cover_image(uploaded_file):
    """
    アップロードされた画像をアスペクト比を維持したまま
    800x480にリサイズし、はみ出た部分を中央で切り取って（グレースケール化）返す
    """
    if uploaded_file is None:
        return None
        
    suffix = os.path.splitext(uploaded_file.name)[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    
    # 画像を開く
    img = Image.open(uploaded_file)
    
    # ターゲットとするサイズ
    target_width = 480
    target_height = 800
    
    # 1. 元の画像とターゲットの比率を計算
    orig_width, orig_height = img.size
    orig_aspect = orig_width / orig_height
    target_aspect = target_width / target_height
    
    # 2. 比率を維持したまま、ターゲットサイズを「完全に覆う」大きさを計算
    if orig_aspect > target_aspect:
        # 元画像の方が横長の場合 → 縦幅をターゲットに合わせる
        new_height = target_height
        new_width = int(target_height * orig_aspect)
    else:
        # 元画像の方が縦長の場合 → 横幅をターゲットに合わせる
        new_width = target_width
        new_height = int(target_width / orig_aspect)
        
    # 比率維持のまま一旦リサイズ（ここではまだはみ出ている）
    resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 3. 中央部分を切り取る（クロップ）ための座標を計算
    left = (new_width - target_width) / 2
    top = (new_height - target_height) / 2
    right = (new_width + target_width) / 2
    bottom = (new_height + target_height) / 2
    
    # 中央で綺麗に切り抜き
    cropped_img = resized_img.crop((left, top, right, bottom))
    
    # 4. グレースケールに変換して保存
    gray_img = cropped_img.convert("L")
    gray_img.save(tmp.name)
    tmp.close()
    
    return tmp.name


# セッション初期化
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "current_page" not in st.session_state:
    st.session_state.current_page = "login"
if "active_url" not in st.session_state:
    st.session_state.active_url = ""

if st.session_state.user_email is None:
    saved_email = cookies.get("user_email")
    if saved_email:
        user = cached_get_user_by_email(saved_email)
        if user:
            st.session_state.user_email = saved_email
            st.session_state.current_page = "dashboard"


def log(msg):
    if "log_area" in st.session_state:
        st.session_state.log_area.text(msg)


def progress(i, percent, title):
    if "progress_bar" in st.session_state:
        st.session_state.progress_bar.progress(min(int(percent), 100))
        st.session_state.log_area.text(
            f"{i}話目取得中 ({percent:.1f}%): {title}"
        )


def inject_app_styles():
    """本棚を中心にしたレスポンシブUIの共通スタイル。"""
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1180px;
            padding-top: 2rem;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 16px;
            border-color: rgba(128, 128, 128, 0.25);
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.05);
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover {
            border-color: rgba(255, 75, 75, 0.45);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.09);
        }
        .library-title {
            margin: 0;
            font-size: clamp(1.7rem, 4vw, 2.5rem);
            font-weight: 750;
        }
        .book-meta {
            color: rgba(128, 128, 128, 0.95);
            font-size: 0.85rem;
        }
        @media (max-width: 640px) {
            .block-container { padding: 1rem 0.8rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_book_card(novel):
    """登録済み作品を表紙付きカードとして表示する。"""
    with st.container(border=True):
        cover_col, detail_col = st.columns([1, 2.2], vertical_alignment="center")

        with cover_col:
            cover_data = novel.get("cover_image")
            if cover_data:
                try:
                    st.image(base64.b64decode(cover_data), use_container_width=True)
                except Exception:
                    st.caption("📕 表紙なし")
            else:
                st.markdown("### 📕")
                st.caption("表紙なし")

        with detail_col:
            st.markdown(f"#### {novel['title']}")
            chapter_count = int(novel.get("latest_chapter") or 0)
            st.markdown(
                f'<div class="book-meta">保存済み {chapter_count} 話</div>',
                unsafe_allow_html=True,
            )

            update_results = st.session_state.get("check_results") or []
            update_result = next(
                (
                    result for result in update_results
                    if result.get("id") == novel.get("id")
                ),
                None,
            )
            if update_result and update_result.get("has_update"):
                if st.button(
                    f"🆕 更新あり ＋{update_result['added']}話",
                    key=f"update_novel_{novel['id']}",
                    use_container_width=True,
                ):
                    st.session_state.active_url = novel["url"]
                    st.session_state.active_novel_id = novel["id"]
                    st.session_state.checked_latest_total = update_result["current_chapters"]
                    st.session_state.current_page = "update_novel"
                    st.rerun()
            elif update_result and not update_result.get("error"):
                st.caption("✓ 最新")
            elif update_result and update_result.get("error"):
                st.caption("⚠ 更新確認に失敗")

            if st.button(
                "開く",
                key=f"open_novel_{novel['id']}",
                use_container_width=True,
                type="primary",
            ):
                st.session_state.pop("checked_latest_total", None)
                st.session_state.active_url = novel["url"]
                st.session_state.current_page = "download_and_manage"
                st.rerun()

            with st.popover("•••", use_container_width=True):
                st.caption(novel["url"])
                if st.button("本棚から削除", key=f"delete_novel_{novel['id']}"):
                    try:
                        if delete_novel(novel["id"]):
                            cached_get_user_novels.clear()
                            cached_get_latest_chapter_count.clear()
                            st.success(f"「{novel['title']}」を削除しました。")
                            st.rerun()
                        st.error("削除に失敗しました。")
                    except Exception as exc:
                        st.error(f"削除中にエラーが発生しました: {exc}")


def run_update_checks(novels):
    """ホーム画面上で全作品を順番に確認し、カード用の結果を返す。"""
    if not novels:
        return []

    cached_get_latest_chapter_count.clear()
    progress_bar = st.progress(0)
    status_text = st.empty()
    results = []
    total_novels = len(novels)

    for index, novel in enumerate(novels):
        status_text.text(
            f"更新確認中: {novel['title']} ({index + 1}/{total_novels})"
        )
        try:
            current_chapters = cached_get_latest_chapter_count(novel["url"])
            saved_chapters = int(novel.get("latest_chapter") or 0)
            has_update = current_chapters > saved_chapters
            results.append({
                "id": novel["id"],
                "title": novel["title"],
                "url": novel["url"],
                "saved_chapters": saved_chapters,
                "current_chapters": current_chapters,
                "has_update": has_update,
                "added": max(current_chapters - saved_chapters, 0),
                "error": None,
            })
        except Exception as exc:
            results.append({
                "id": novel["id"],
                "title": novel["title"],
                "url": novel["url"],
                "has_update": False,
                "error": str(exc),
            })
        progress_bar.progress((index + 1) / total_novels)

    progress_bar.empty()
    status_text.empty()
    return results


def login_page():
    st.title("📚 Novel Downloader")
    st.subheader("ログイン")

    with st.form("login_form"):
        email_login = st.text_input("メールアドレス", key="email_login")
        login_submitted = st.form_submit_button("ログイン")

    if login_submitted:
        if email_login:
            user = cached_get_user_by_email(email_login)
            if user:
                st.success("ログインしました")
                cookies["user_email"] = email_login
                cookies.save()
                st.session_state.user_email = email_login
                st.session_state.current_page = "dashboard"
                st.session_state.pop("check_results", None)
                st.rerun()
            else:
                st.error("このメールアドレスは登録されていません")
        else:
            st.error("メールアドレスを入力してください")

    st.divider()
    st.subheader("新規登録")

    with st.form("register_form"):
        email_new = st.text_input("メールアドレス（新規）", key="email_new")
        register_submitted = st.form_submit_button("登録")

    if register_submitted:
        if email_new:
            success, msg = register_user(email_new)
            if success:
                st.success(msg)
                cached_get_user_novels.clear()
                cached_get_latest_chapter_count.clear()
                cookies["user_email"] = email_new
                cookies.save()
                st.session_state.user_email = email_new
                st.session_state.current_page = "dashboard"
                st.session_state.pop("check_results", None)
                st.rerun()
            else:
                st.error(msg)
        else:
            st.error("メールアドレスを入力してください")


def dashboard_page():
    """ダッシュボード"""
    inject_app_styles()

    title_col, download_col = st.columns([5, 1.5], vertical_alignment="center")
    with title_col:
        st.markdown('<h1 class="library-title">📚 本棚</h1>', unsafe_allow_html=True)
        st.caption(f"同期中: {st.session_state.user_email}")
    with download_col:
        if st.button("＋ ダウンロード", use_container_width=True, type="primary"):
            st.session_state.pop("checked_latest_total", None)
            st.session_state.active_url = ""
            st.session_state.current_page = "download_and_manage"
            st.rerun()
    with st.expander("設定・アカウント"):
        account_col, settings_col, logout_col = st.columns([3, 1, 1])
        with account_col:
            st.write(st.session_state.user_email)
        with settings_col:
            if st.button("⚙️ 設定", use_container_width=True):
                st.session_state.current_page = "settings"
                st.rerun()
        with logout_col:
            if st.button("ログアウト", use_container_width=True):
                st.session_state.user_email = None
                st.session_state.current_page = "login"
                st.session_state.pop("check_results", None)
                cookies["user_email"] = ""
                cookies.save()
                st.rerun()

    st.subheader("ダウンロード済み", divider="gray")
    novels = normalize_novel_rows(
        cached_get_user_novels(st.session_state.user_email)
    )
    
    if not novels:
        st.info("登録済み小説がありません")
        return

    should_check_updates = (
        st.session_state.get("check_results") is None
        or st.session_state.pop("update_requested", False)
    )
    if should_check_updates:
        with st.status("本棚を更新しています", expanded=True) as update_status:
            st.session_state.check_results = run_update_checks(novels)
            failures = sum(
                1 for result in st.session_state.check_results
                if result.get("error")
            )
            updates = sum(
                1 for result in st.session_state.check_results
                if result.get("has_update")
            )
            if failures:
                update_status.update(
                    label=f"更新確認完了：{updates}作品に更新、{failures}作品で失敗",
                    state="error",
                    expanded=False,
                )
            else:
                update_status.update(
                    label=f"更新確認完了：{updates}作品に更新があります",
                    state="complete",
                    expanded=False,
                )

    check_results = st.session_state.get("check_results") or []
    if check_results:
        updates = sum(1 for result in check_results if result.get("has_update"))
        failures = sum(1 for result in check_results if result.get("error"))
        summary_col, retry_col = st.columns([4, 1])
        with summary_col:
            if updates:
                st.success(f"{updates}作品に更新があります。カードから作品を開いてください。")
            elif not failures:
                st.info("すべて最新です。")
            if failures:
                st.warning(f"{failures}作品の確認に失敗しました。時間を空けて再試行してください。")
        with retry_col:
            if st.button("再確認", use_container_width=True):
                st.session_state.update_requested = True
                st.rerun()
    
    for index in range(0, len(novels), 2):
        left, right = st.columns(2, gap="large")
        with left:
            render_book_card(novels[index])
        if index + 1 < len(novels):
            with right:
                render_book_card(novels[index + 1])


def download_and_manage_page(update_only=False):
    """【統合版】ダウンロード＆小説管理ページ"""
    st.title("🔄 作品を更新" if update_only else "📥 小説ダウンロード ＆ 管理")
    
    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()
        
    st.divider()
    
    # 新規追加では入力、更新専用画面では選択済み作品を固定表示する
    if update_only:
        url = st.session_state.active_url.strip()
        st.caption(f"更新対象: {url}")
    else:
        url = st.text_input(
            "小説URL",
            value=st.session_state.active_url,
            placeholder="https://ncode.syosetu.com/... or https://kakuyomu.jp/works/..."
        ).strip()
    
    if not url:
        st.info("小説のURLを入力すると、ダウンロードおよび各種管理機能が利用可能になります。")
        return

    # ─── 登録状態の自動自動判別 ───
    novels = cached_get_user_novels(st.session_state.user_email)
    matched_novel = None
    if novels:
        for n in novels:
            if n['url'] == url:
                matched_novel = n
                break
                
    is_already_registered = (matched_novel is not None)

    if update_only and not is_already_registered:
        st.error("更新対象の作品が本棚に見つかりません。ホームへ戻って選び直してください。")
        return
    
    # 画面を2つのカラムに分ける
    col_info, col_dl = st.columns([1, 1])
    
    with col_info:
        st.subheader("作品ステータス ＆ 表紙管理")
        if is_already_registered:
            st.success("✅ この小説はすでに登録されています。")
            st.write(f"**登録タイトル:** {matched_novel['title']}")
            
            # 現在の表紙表示
            if matched_novel.get('cover_image'):
                st.write("**現在の表紙:**")
                cover_bytes = base64.b64decode(matched_novel['cover_image'])
                st.image(cover_bytes, width=200)
            else:
                st.info("表紙がまだ設定されていません")
        else:
            st.warning("🆕 新規登録される小説です（ダウンロード完了時にDBへ追加されます）。")
            
        # 表紙選択（新規・更新共通）
        cover = st.file_uploader(
            "表紙画像を選択 (オプション)",
            type=["jpg", "jpeg", "bmp"],
            key=f"cover_uploader_{url}"
        )
        
        # 登録済みかつ表紙が新たに選ばれた場合、その場での単体更新ボタンを表示
        if is_already_registered and cover:
            if st.button("表紙画像のみを今すぐ更新"):
                processed_path = process_cover_image(cover)
                if processed_path and os.path.exists(processed_path):
                    with open(processed_path, "rb") as f:
                        processed_bytes = BytesIO(f.read())
                    if update_cover_image(matched_novel['id'], processed_bytes):
                        os.unlink(processed_path)
                        cached_get_user_novels.clear()
                        st.success("表紙画像を更新しました！")
                        st.rerun()
                    else:
                        os.unlink(processed_path)
                        st.error("表紙の更新に失敗しました")

    with col_dl:
        st.subheader("ダウンロード設定")
        try:
            # ─── 修正：引き継いだ話数があればそれを使い、無ければネットから取得する ───
            if "checked_latest_total" in st.session_state and st.session_state.active_url == url:
                current_total = st.session_state.checked_latest_total
            else:
                current_total = cached_get_latest_chapter_count(url)
            st.write(f"**Web上の全話数:** {current_total} 話")
        except Exception as e:
            st.error(f"全話数の取得に失敗しました: {e}")
            return
            
        saved_total = matched_novel.get('latest_chapter', 0) if is_already_registered else 0
        
        if is_already_registered:
            if current_total > saved_total:
                st.success(f"📈 {current_total - saved_total}話の更新があります (保存済み: {saved_total}話)")
            else:
                st.info(f"✅ 最新の状態です (保存済み: {saved_total}話)")
                
        download_mode = st.radio(
            "作成方法",
            ["追加分のみ取得（完全版EPUB）", "全話を再取得"]
            if is_already_registered
            else ["全話を取得"],
            horizontal=True,
            help=(
                "最新版は保存済み本文を再利用し、新着話だけ取得して完全版EPUBを作ります。"
            ),
        )

        disable_download = False
        if is_already_registered and current_total <= saved_total:
            st.info("更新はありません。保存済み本文から完全版EPUBを再作成できます。")
            
        if st.button("📖 ダウンロード開始", disabled=disable_download):
            cover_path = None
            cover_bytes = None
            
            # 表紙の処理
            if cover:
                cover_path = process_cover_image(cover)
                if cover_path and os.path.exists(cover_path):
                    with open(cover_path, "rb") as f:
                        cover_bytes = BytesIO(f.read())
            elif is_already_registered and matched_novel.get('cover_image'):
                # 表紙を新しく選んでおらず、既に既存の表紙がある場合はそれを流用
                cover_bytes = base64.b64decode(matched_novel['cover_image'])
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                tmp.write(cover_bytes)
                tmp.close()
                cover_path = tmp.name
                cover_bytes = BytesIO(cover_bytes)

            try:
                with st.spinner("EPUB生成中..."):
                    st.session_state.progress_bar = st.progress(0)
                    st.session_state.log_area = st.empty()

                    cached_chapters = []
                    use_cache = (
                        is_already_registered
                        and download_mode == "追加分のみ取得（完全版EPUB）"
                    )
                    if use_cache:
                        try:
                            cached_chapters = get_cached_chapters(
                                st.session_state.user_email,
                                matched_novel["id"],
                            )
                        except Exception:
                            st.warning(
                                "本文キャッシュがまだ利用できないため、今回は全話を取得します。"
                            )

                    newly_fetched_chapters = []
                    
                    output_folder = create_epub(
                        url,
                        cover_path=cover_path,
                        progress_callback=progress,
                        log_callback=log,
                        start_episode=1,
                        cached_episodes=cached_chapters,
                        chapter_callback=newly_fetched_chapters.append,
                    )
                    
                work_title = os.path.basename(output_folder)
                st.info(f"📖 作品名: **{work_title}**")
                
                novel_id = matched_novel['id'] if is_already_registered else None
                registration_success = True
                
                # 新規登録処理
                if not is_already_registered:
                    success, msg, new_id = register_novel(
                        st.session_state.user_email,
                        url,
                        work_title,
                        cover_bytes
                    )
                    if success:
                        novel_id = new_id
                    else:
                        st.error(msg)
                        registration_success = False
                else:
                    # 既に登録されている場合で、今回新しい表紙がアップロードされていたら上書き更新
                    if cover and cover_bytes:
                        update_cover_image(novel_id, cover_bytes)
                        
                if registration_success and novel_id is not None:
                    if newly_fetched_chapters:
                        try:
                            upsert_cached_chapters(
                                st.session_state.user_email,
                                novel_id,
                                newly_fetched_chapters,
                            )
                        except Exception as exc:
                            st.warning(f"本文キャッシュの保存に失敗しました: {exc}")

                    epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
                    cached_get_latest_chapter_count.clear()
                    actual_total = cached_get_latest_chapter_count(url)
                    
                    update_latest_chapter(novel_id, actual_total)
                    cached_get_user_novels.clear()

                    # 更新完了後、ホームのラベルをその場で「最新」へ切り替える。
                    for result in st.session_state.get("check_results") or []:
                        if result.get("id") == novel_id:
                            result["saved_chapters"] = actual_total
                            result["current_chapters"] = actual_total
                            result["has_update"] = False
                            result["added"] = 0
                            result["error"] = None
                            break
                    
                    if cover_path and os.path.exists(cover_path):
                        os.unlink(cover_path)
                        
                    # ZIP作成
                    zip_path = os.path.join(output_folder, f"{work_title}.zip")
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                        for epub_file in epub_files:
                            full_path = os.path.join(output_folder, epub_file)
                            zipf.write(full_path, arcname=epub_file)
                            
                    with open(zip_path, "rb") as f:
                        st.download_button(
                            label="📥 ZIPをダウンロード",
                            data=f.read(),
                            file_name=f"{work_title}.zip",
                            mime="application/zip"
                        )
                        
                    record_download(st.session_state.user_email, novel_id, actual_total, zip_path)
                    st.success("最新のデータのダウンロード準備が整いました！")
                else:
                    if cover_path and os.path.exists(cover_path):
                        os.unlink(cover_path)
                        
            except Exception as e:
                st.error(f"エラー: {str(e)}")
                if cover_path and os.path.exists(cover_path):
                    os.unlink(cover_path)


def settings_page():
    st.title("⚙️ 設定")
    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()
    st.divider()


def update_novel_page():
    """本棚の更新ラベルから開く、登録済み作品専用画面。"""
    download_and_manage_page(update_only=True)


# ページルーティング表示
if st.session_state.user_email is None:
    login_page()
elif st.session_state.current_page == "login":
    login_page()
elif st.session_state.current_page == "dashboard":
    dashboard_page()
elif st.session_state.current_page == "download_and_manage":
    download_and_manage_page()
elif st.session_state.current_page == "update_novel":
    update_novel_page()
elif st.session_state.current_page == "settings":
    settings_page()

