import streamlit as st
import tempfile
import os
import zipfile
import base64
import html
from io import BytesIO
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from novel_downloader import create_epub, get_latest_chapter_count
from epub_builder import safe_filename, write_epub
from networking import configure_networking
from database import (
    get_user_by_email,
    register_user,
    get_user_novels,
    register_novel,
    update_latest_chapter,
    save_update_check_result,
    record_download,
    delete_novel,
    update_cover_image,
    get_cached_chapters,
    has_cached_chapters,
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


@st.cache_data(ttl=300)
def cached_has_chapter_cache(email, novel_id):
    return has_cached_chapters(email, novel_id)


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


def cookie_number(key, default, cast, minimum, maximum):
    """暗号化Cookieから安全に数値設定を復元する。"""
    try:
        raw_value = cookies.get(key)
        value = cast(default if raw_value in (None, "") else raw_value)
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


if "network_interval" not in st.session_state:
    st.session_state.network_interval = cookie_number(
        "network_interval", 2.5, float, 1.5, 10.0
    )
if "network_jitter" not in st.session_state:
    st.session_state.network_jitter = cookie_number(
        "network_jitter", 0.75, float, 0.0, 3.0
    )
if "network_retries" not in st.session_state:
    st.session_state.network_retries = cookie_number(
        "network_retries", 3, int, 1, 5
    )
if "network_backoff" not in st.session_state:
    st.session_state.network_backoff = cookie_number(
        "network_backoff", 1.5, float, 0.5, 5.0
    )

configure_networking(
    st.session_state.network_interval,
    st.session_state.network_jitter,
    st.session_state.network_retries,
    st.session_state.network_backoff,
)

if st.session_state.user_email is None:
    saved_email = cookies.get("user_email")
    if saved_email:
        try:
            user = cached_get_user_by_email(saved_email)
            if user:
                st.session_state.user_email = saved_email
                st.session_state.current_page = "dashboard"
        except Exception as exc:
            st.session_state.login_connection_error = str(exc)


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
        [data-testid="stVerticalBlockBorderWrapper"]:has(.book-card-marker) {
            height: 390px;
            min-height: 390px;
            overflow-y: auto;
        }
        .book-card-marker {
            display: none;
        }
        .book-cover-marker {
            display: none;
        }
        .library-row-marker {
            display: none;
        }
        .book-title {
            height: 2.9em;
            line-height: 1.45;
            overflow: hidden;
            display: -webkit-box;
            -webkit-box-orient: vertical;
            -webkit-line-clamp: 2;
            font-size: 1.12rem;
            font-weight: 700;
            cursor: help;
            overflow-wrap: anywhere;
        }
        @media (max-width: 768px) {
            .block-container { padding: 1rem 0.8rem; }
            [data-testid="stHorizontalBlock"]:has(.library-row-marker) {
                display: flex !important;
                flex-direction: column !important;
                flex-wrap: nowrap !important;
                gap: 1rem !important;
            }
            [data-testid="stHorizontalBlock"]:has(.library-row-marker)
            > [data-testid="stColumn"] {
                flex: 1 1 100% !important;
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
            }
            [data-testid="stVerticalBlockBorderWrapper"]:has(.book-card-marker) {
                height: 420px;
                min-height: 420px;
            }
            [data-testid="stHorizontalBlock"]:has(.book-cover-marker):not(:has(.library-row-marker)) {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: flex-start !important;
                gap: 0.75rem !important;
            }
            [data-testid="stHorizontalBlock"]:has(.book-cover-marker):not(:has(.library-row-marker))
            > [data-testid="stColumn"]:first-child {
                flex: 0 0 34% !important;
                width: 34% !important;
                min-width: 0 !important;
            }
            [data-testid="stHorizontalBlock"]:has(.book-cover-marker):not(:has(.library-row-marker))
            > [data-testid="stColumn"]:last-child {
                flex: 1 1 66% !important;
                width: 66% !important;
                min-width: 0 !important;
            }
            [data-testid="stHorizontalBlock"]:has(.book-cover-marker):not(:has(.library-row-marker))
            [data-testid="stImage"] img {
                width: 100% !important;
                max-height: 280px !important;
                object-fit: cover !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_novel_site(novel):
    """作品URLから本棚上のサイト区分を返す。"""
    url = str(novel.get("url") or "").lower()
    if "syosetu.com" in url:
        return "narou"
    if "kakuyomu.jp" in url:
        return "kakuyomu"
    return "other"


def get_default_cover_path(site):
    """配置場所の新旧両方からサイト別デフォルト表紙を探す。"""
    if site not in {"narou", "kakuyomu"}:
        return None
    base_dir = os.path.dirname(os.path.abspath(__file__))
    filename = f"default_{site}.jpg"
    candidates = [
        os.path.join(base_dir, "images", filename),
        os.path.join(base_dir, filename),
        os.path.join(base_dir, "novel_web", "images", filename),
    ]
    return next((path for path in candidates if os.path.exists(path)), None)


def build_cached_epub_archive(novel, cached_chapters):
    """保存済み本文だけから従来形式のEPUB一式をZIPへまとめる。"""
    if not cached_chapters:
        return None

    site = get_novel_site(novel)
    site_name = {
        "narou": "小説家になろう",
        "kakuyomu": "カクヨム",
        "other": "Web小説",
    }[site]
    title = str(novel.get("title") or "Novel")

    volumes = []
    volume_map = {}
    for chapter in cached_chapters:
        if not isinstance(chapter, dict) or not chapter.get("body_html"):
            continue
        volume_title = str(chapter.get("chapter_title") or title)
        if volume_title not in volume_map:
            volume_map[volume_title] = []
            volumes.append((volume_title, volume_map[volume_title]))
        volume_map[volume_title].append({
            "id": chapter.get("episode_id"),
            "title": chapter.get("title") or "無題",
            "body": chapter.get("body_html") or "",
        })

    if not volumes:
        return None

    with tempfile.TemporaryDirectory() as folder:
        cover_path = get_default_cover_path(site)
        cover_data = novel.get("cover_image")
        if cover_data:
            try:
                cover_path = os.path.join(folder, "cover.jpg")
                with open(cover_path, "wb") as cover_file:
                    cover_file.write(base64.b64decode(cover_data))
            except Exception:
                cover_path = get_default_cover_path(site)

        epub_paths = []
        for file_index, (volume_title, episodes) in enumerate(volumes, 1):
            epub_path = write_epub(
                title,
                volume_title,
                episodes,
                file_index,
                folder,
                site_name,
                cover_path,
            )
            if epub_path:
                epub_paths.append(epub_path)

        if not epub_paths:
            return None

        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for epub_path in epub_paths:
                zip_file.write(epub_path, arcname=os.path.basename(epub_path))
        return {
            "data": archive.getvalue(),
            "file_name": f"{safe_filename(title)}_キャッシュ版.zip",
            "chapter_count": len(cached_chapters),
        }


def render_book_card(novel):
    """登録済み作品を表紙付きカードとして表示する。"""
    with st.container(border=True):
        st.markdown('<div class="book-card-marker"></div>', unsafe_allow_html=True)
        cover_col, detail_col = st.columns([1, 2.2], vertical_alignment="center")

        with cover_col:
            st.markdown(
                '<div class="book-cover-marker"></div>',
                unsafe_allow_html=True,
            )
            cover_data = novel.get("cover_image")
            if cover_data:
                try:
                    st.image(base64.b64decode(cover_data), use_container_width=True)
                except Exception:
                    default_cover = get_default_cover_path(get_novel_site(novel))
                    if default_cover:
                        st.image(default_cover, use_container_width=True)
                    else:
                        st.caption("📕 表紙なし")
            else:
                default_cover = get_default_cover_path(get_novel_site(novel))
                if default_cover:
                    st.image(default_cover, use_container_width=True)
                else:
                    st.markdown("### 📕")
                    st.caption("表紙なし")

        with detail_col:
            escaped_title = html.escape(str(novel.get("title") or "タイトル不明"))
            title_attribute = html.escape(
                str(novel.get("title") or "タイトル不明"),
                quote=True,
            )
            st.markdown(
                f'<div class="book-title" title="{title_attribute}">'
                f"{escaped_title}</div>",
                unsafe_allow_html=True,
            )
            site_label = {
                "narou": "🟦 小説家になろう",
                "kakuyomu": "🟨 カクヨム",
                "other": "📖 その他",
            }[get_novel_site(novel)]
            st.caption(site_label)
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

            open_col, cache_col = st.columns(2)
            with open_col:
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
            with cache_col:
                user_email = st.session_state.user_email
                novel_snapshot = dict(novel)
                try:
                    cache_available = cached_has_chapter_cache(
                        user_email,
                        novel["id"],
                    )
                    cache_help = (
                        "保存済み本文だけからEPUBを再作成して、"
                        "そのままZIPをダウンロードします。"
                        if cache_available
                        else "本文キャッシュがありません。一度作品を更新してください。"
                    )
                except Exception:
                    cache_available = False
                    cache_help = "本文キャッシュの確認に失敗しました。"

                def generate_cached_zip():
                    cached_chapters = get_cached_chapters(
                        user_email,
                        novel_snapshot["id"],
                    )
                    payload = build_cached_epub_archive(
                        novel_snapshot,
                        cached_chapters,
                    )
                    if not payload:
                        raise RuntimeError(
                            "本文キャッシュがありません。"
                            "一度作品を更新してください。"
                        )
                    return payload["data"]

                st.download_button(
                    "再ダウンロード",
                    data=generate_cached_zip,
                    file_name=(
                        f"{safe_filename(str(novel.get('title') or 'Novel'))}"
                        "_キャッシュ版.zip"
                    ),
                    mime="application/zip",
                    key=f"download_cached_{novel['id']}",
                    use_container_width=True,
                    help=cache_help,
                    disabled=not cache_available,
                    on_click="ignore",
                )

            with st.popover("•••", use_container_width=True):
                st.caption(novel["url"])
                if st.button("本棚から削除", key=f"delete_novel_{novel['id']}"):
                    try:
                        if delete_novel(novel["id"]):
                            cached_get_user_novels.clear()
                            cached_get_latest_chapter_count.clear()
                            cached_has_chapter_cache.clear()
                            st.success(f"「{novel['title']}」を削除しました。")
                            st.rerun()
                        st.error("削除に失敗しました。")
                    except Exception as exc:
                        st.error(f"削除中にエラーが発生しました: {exc}")


def render_library_section(title, external_label, external_url, novels):
    """サイト別の見出しと2列の作品カードを表示する。"""
    title_col, link_col = st.columns([4, 1.6], vertical_alignment="center")
    with title_col:
        st.markdown(f"### {title}　`{len(novels)}冊`")
    with link_col:
        if external_url:
            st.link_button(
                external_label,
                external_url,
                use_container_width=True,
            )

    if not novels:
        st.caption("このサイトの登録作品はまだありません。")
        return

    for index in range(0, len(novels), 2):
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown(
                '<div class="library-row-marker"></div>',
                unsafe_allow_html=True,
            )
            render_book_card(novels[index])
        if index + 1 < len(novels):
            with right:
                render_book_card(novels[index + 1])


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
            try:
                save_update_check_result(
                    novel["id"],
                    current_chapters=current_chapters,
                )
            except Exception:
                # サイト側の確認結果は表示し、DB保存失敗で結果を重複させない。
                pass
        except Exception as exc:
            results.append({
                "id": novel["id"],
                "title": novel["title"],
                "url": novel["url"],
                "has_update": False,
                "error": str(exc),
            })
            try:
                save_update_check_result(
                    novel["id"],
                    error=exc,
                )
            except Exception:
                # 更新確認自体のエラーを優先して表示する。
                pass
        progress_bar.progress((index + 1) / total_novels)

    progress_bar.empty()
    status_text.empty()
    return results


JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")


def japan_today_iso():
    """Streamlit CloudのUTC設定に左右されない日本の日付を返す。"""
    return datetime.now(JAPAN_TIMEZONE).date().isoformat()


def checked_today_in_japan(novel):
    """保存済みの自動確認が日本時間の今日行われたか判定する。"""
    raw_value = novel.get("last_update_checked_at")
    if not raw_value:
        return False
    try:
        checked_at = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return checked_at.astimezone(JAPAN_TIMEZONE).date().isoformat() == japan_today_iso()
    except (TypeError, ValueError):
        return False


def result_from_saved_check(novel):
    """novels行に保存した本日の確認結果をカード表示用へ戻す。"""
    saved_chapters = int(novel.get("latest_chapter") or 0)
    error = novel.get("update_check_error")
    checked_latest = novel.get("checked_latest_chapter")
    current_chapters = (
        int(checked_latest) if checked_latest is not None else saved_chapters
    )
    return {
        "id": novel["id"],
        "title": novel.get("title") or "タイトル不明",
        "url": novel.get("url") or "",
        "saved_chapters": saved_chapters,
        "current_chapters": current_chapters,
        "has_update": not error and current_chapters > saved_chapters,
        "added": max(current_chapters - saved_chapters, 0) if not error else 0,
        "error": error,
    }


def load_or_run_daily_update_checks(novels):
    """本日分はDBから再利用し、未確認作品だけ通信して結果を保存する。"""
    saved_results = {}
    unchecked_novels = []
    for novel in novels:
        if checked_today_in_japan(novel):
            saved_results[novel["id"]] = result_from_saved_check(novel)
        else:
            unchecked_novels.append(novel)

    if unchecked_novels:
        for result in run_update_checks(unchecked_novels):
            saved_results[result["id"]] = result

    return [
        saved_results[novel["id"]]
        for novel in novels
        if novel.get("id") in saved_results
    ]


def login_page():
    st.title("📚 Novel Downloader")
    st.subheader("ログイン")

    with st.form("login_form"):
        email_login = st.text_input("メールアドレス", key="email_login")
        login_submitted = st.form_submit_button("ログイン")

    if login_submitted:
        if email_login:
            try:
                user = cached_get_user_by_email(email_login)
                if user:
                    st.success("ログインしました")
                    cookies["user_email"] = email_login
                    cookies.save()
                    st.session_state.user_email = email_login
                    st.session_state.current_page = "dashboard"
                    st.session_state.pop("check_results", None)
                    st.session_state.pop("login_connection_error", None)
                    st.rerun()
                else:
                    st.error("このメールアドレスは登録されていません")
            except Exception:
                st.error(
                    "Supabaseへ接続できません。"
                    "StreamlitのSecretsにあるURLとAPIキーを確認してください。"
                )
        else:
            st.error("メールアドレスを入力してください")

    if st.session_state.pop("login_connection_error", None):
        st.warning(
            "保存済みログインの確認時にSupabaseへ接続できませんでした。"
            "URLとAPIキーを確認してください。"
        )

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

    title_col, settings_col, download_col = st.columns(
        [4.5, 1.2, 1.7], vertical_alignment="center"
    )
    with title_col:
        st.markdown('<h1 class="library-title">📚 本棚</h1>', unsafe_allow_html=True)
        st.caption(f"同期中: {st.session_state.user_email}")
    with settings_col:
        if st.button("⚙️ 設定", use_container_width=True):
            st.session_state.current_page = "settings"
            st.rerun()
    with download_col:
        if st.button("＋ ダウンロード", use_container_width=True, type="primary"):
            st.session_state.pop("checked_latest_total", None)
            st.session_state.active_url = ""
            st.session_state.current_page = "download_and_manage"
            st.rerun()
    with st.expander("アカウント"):
        account_col, logout_col = st.columns([4, 1])
        with account_col:
            st.write(st.session_state.user_email)
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

    manual_update_requested = st.session_state.pop("update_requested", False)
    results_are_from_today = (
        st.session_state.get("check_results_date") == japan_today_iso()
    )
    should_check_updates = (
        manual_update_requested
        or st.session_state.get("check_results") is None
        or not results_are_from_today
    )
    if should_check_updates:
        with st.status("本棚を更新しています", expanded=True) as update_status:
            if manual_update_requested:
                st.session_state.check_results = run_update_checks(novels)
            else:
                st.session_state.check_results = load_or_run_daily_update_checks(novels)
            st.session_state.check_results_date = japan_today_iso()
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
    
    narou_novels = [
        novel for novel in novels if get_novel_site(novel) == "narou"
    ]
    kakuyomu_novels = [
        novel for novel in novels if get_novel_site(novel) == "kakuyomu"
    ]
    other_novels = [
        novel for novel in novels if get_novel_site(novel) == "other"
    ]

    render_library_section(
        "小説家になろう作品",
        "「小説家になろう」へ ↗",
        "https://syosetu.com/",
        narou_novels,
    )
    st.divider()
    render_library_section(
        "カクヨム作品",
        "「カクヨム」へ ↗",
        "https://kakuyomu.jp/",
        kakuyomu_novels,
    )
    if other_novels:
        st.divider()
        render_library_section(
            "その他の作品",
            "",
            None,
            other_novels,
        )


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
    novels = normalize_novel_rows(
        cached_get_user_novels(st.session_state.user_email)
    )
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
                            cached_has_chapter_cache.clear()
                        except Exception as exc:
                            st.warning(f"本文キャッシュの保存に失敗しました: {exc}")

                    epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
                    cached_get_latest_chapter_count.clear()
                    actual_total = cached_get_latest_chapter_count(url)
                    
                    update_latest_chapter(novel_id, actual_total)
                    try:
                        save_update_check_result(
                            novel_id,
                            current_chapters=actual_total,
                        )
                    except Exception:
                        pass
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
    st.subheader("アクセス設定")
    st.caption(
        "小説サイトへの負荷とアクセス制限を避けるための設定です。"
        "短すぎる間隔には設定できません。"
    )

    with st.form("network_settings_form"):
        interval = st.slider(
            "最小アクセス間隔（秒）",
            min_value=1.5,
            max_value=10.0,
            value=float(st.session_state.network_interval),
            step=0.5,
            help="各ページへアクセスする前に最低限待つ時間です。推奨は2.5秒以上です。",
        )
        jitter = st.slider(
            "ランダム待機時間（最大秒）",
            min_value=0.0,
            max_value=3.0,
            value=float(st.session_state.network_jitter),
            step=0.25,
            help="一定間隔にならないよう、最小間隔へランダムに加える時間です。",
        )
        retries = st.slider(
            "通信失敗時の再試行回数",
            min_value=1,
            max_value=5,
            value=int(st.session_state.network_retries),
            step=1,
        )
        backoff = st.slider(
            "再試行の待機倍率",
            min_value=0.5,
            max_value=5.0,
            value=float(st.session_state.network_backoff),
            step=0.5,
            help="失敗が続くほど次の再試行まで長く待つための倍率です。",
        )
        saved = st.form_submit_button(
            "設定を保存", type="primary", use_container_width=True
        )

    if saved:
        st.session_state.network_interval = interval
        st.session_state.network_jitter = jitter
        st.session_state.network_retries = retries
        st.session_state.network_backoff = backoff
        cookies["network_interval"] = str(interval)
        cookies["network_jitter"] = str(jitter)
        cookies["network_retries"] = str(retries)
        cookies["network_backoff"] = str(backoff)
        cookies.save()
        configure_networking(interval, jitter, retries, backoff)
        cached_get_latest_chapter_count.clear()
        st.success("アクセス設定を保存しました。次の通信から反映されます。")

    st.info(
        "おすすめ: 最小間隔 2.5秒、ランダム待機 0.75秒、"
        "再試行 3回、待機倍率 1.5"
    )

    if st.button("おすすめ設定に戻す", use_container_width=True):
        st.session_state.network_interval = 2.5
        st.session_state.network_jitter = 0.75
        st.session_state.network_retries = 3
        st.session_state.network_backoff = 1.5
        cookies["network_interval"] = "2.5"
        cookies["network_jitter"] = "0.75"
        cookies["network_retries"] = "3"
        cookies["network_backoff"] = "1.5"
        cookies.save()
        configure_networking(2.5, 0.75, 3, 1.5)
        cached_get_latest_chapter_count.clear()
        st.rerun()

    st.divider()
    st.subheader("アカウント")
    st.write(st.session_state.user_email)
    if st.button("ログアウト", use_container_width=True):
        st.session_state.user_email = None
        st.session_state.current_page = "login"
        st.session_state.pop("check_results", None)
        cookies["user_email"] = ""
        cookies.save()
        st.rerun()


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
