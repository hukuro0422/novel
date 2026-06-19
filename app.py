import streamlit as st
import tempfile
import os
import zipfile
import time
from datetime import datetime
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
    get_download_history
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
    """アップロードされた画像を400x800のグレースケールに変換し、一時ファイルのパスを返す"""
    if uploaded_file is None:
        return None
        
    suffix = os.path.splitext(uploaded_file.name)[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    
    img = Image.open(uploaded_file)
    gray_img = img.convert("L")
    resized_img = gray_img.resize((400, 800), Image.Resampling.LANCZOS)
    
    resized_img.save(tmp.name)
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
                st.rerun()
            else:
                st.error(msg)
        else:
            st.error("メールアドレスを入力してください")


def dashboard_page():
    """ダッシュボード"""
    st.title("📚 ダッシュボード")
    st.write(f"ログイン中: {st.session_state.user_email}")
    
    if st.button("ログアウト"):
        st.session_state.user_email = None
        st.session_state.current_page = "login"
        cookies["user_email"] = ""
        cookies.save()
        st.success("ログアウトしました")
        st.rerun()
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📖 新規小説を追加"):
            if "checked_latest_total" in st.session_state:
                del st.session_state['checked_latest_total']
            st.session_state.active_url = ""  # URL入力を空にする
            st.session_state.current_page = "download_and_manage"
            st.rerun()
    with col2:
        if st.button("⚙️ 設定"):
            st.session_state.current_page = "settings"
            st.rerun()
    with col3:
        if st.button("🔄 全て更新チェック"):
            st.session_state.current_page = "update_check"
            st.rerun()
    
    st.divider()
    st.subheader("登録済み小説")
    novels = cached_get_user_novels(st.session_state.user_email)
    
    if not novels:
        st.info("登録済み小説がありません")
        return
    
    for novel in novels:
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            st.write(f"**{novel['title']}**")
            st.caption(f"URL: {novel['url']}")
            if novel.get('last_downloaded_at'):
                st.caption(f"更新日: {novel['last_downloaded_at'][:10]}")
            elif novel.get('registered_at'):
                st.caption(f"更新日: {novel['registered_at'][:10]}")
        
        with col2:
            if st.button("📥 ダウンロード / 管理", key=f"download_{novel['id']}"):
                if "checked_latest_total" in st.session_state:
                    del st.session_state["checked_latest_total"]
                st.session_state.active_url = novel['url']  # 該当URLを渡す
                st.session_state.current_page = "download_and_manage"
                st.rerun()
        
        with col3:
            if st.button("🗑️ 削除", key=f"delete_novel_{novel['id']}"):
                try:
                    success = delete_novel(novel['id'])
                    if success:
                        st.success(f"「{novel['title']}」を削除しました。")
                        cached_get_user_novels.clear()
                        cached_get_latest_chapter_count.clear()
                        st.rerun()
                    else:
                        st.error("削除に失敗しました。もう一度お試しください。")
                except Exception as e:
                    st.error(f"削除中にエラーが発生しました: {e}")
        st.divider()


def download_and_manage_page():
    """【統合版】ダウンロード＆小説管理ページ"""
    st.title("📥 小説ダウンロード ＆ 管理")
    
    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()
        
    st.divider()
    
    # URL入力欄（新規追加から来たら空、一覧から来たら自動入力）
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
            "ダウンロード方法",
            ["更新分のみ", "全話"] if is_already_registered else ["全話"],
            horizontal=True
        )
        
        disable_download = (is_already_registered and download_mode == "更新分のみ" and current_total <= saved_total)
        if disable_download:
            st.warning("更新がないため「更新分のみ」は選択できません。")
            
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
                    
                    start_episode = (saved_total + 1) if (is_already_registered and download_mode == "更新分のみ") else 1
                    
                    output_folder = create_epub(
                        url,
                        cover_path=cover_path,
                        progress_callback=progress,
                        log_callback=log,
                        start_episode=start_episode
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
                    epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
                    cached_get_latest_chapter_count.clear()
                    actual_total = cached_get_latest_chapter_count(url)
                    
                    update_latest_chapter(novel_id, actual_total)
                    cached_get_user_novels.clear()
                    
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


def update_check_page():
    st.title("🔄 全て更新チェック")

    if st.button("← ダッシュボードに戻る"):
        st.session_state.pop("check_results", None)
        st.session_state.current_page = "dashboard"
        st.rerun()

    st.divider()

    # セッション状態の初期化
    if "check_results" not in st.session_state:
        st.session_state.check_results = None

    # 【状態1】まだチェックしていない場合、ボタンを表示
    if st.session_state.check_results is None:
        if st.button("🔍 更新チェック開始"):
            novels = cached_get_user_novels(st.session_state.user_email)
            if not novels:
                st.info("登録済み小説がありません")
                return

            st.subheader("更新チェック中...")
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_novels = len(novels)
            
            results = []

            # ─── このループは「チェック開始」を押した時の1回しか走りません ───
            for i, novel in enumerate(novels):
                status_text.text(f"チェック中: {novel['title']} ({i+1}/{total_novels})")
                try:
                    current_chapters = cached_get_latest_chapter_count(novel['url'])
                    has_update = current_chapters > novel['latest_chapter']
                    added = current_chapters - novel['latest_chapter'] if has_update else 0
                    
                    results.append({
                        "id": novel["id"],
                        "title": novel["title"],
                        "url": novel["url"],
                        "saved_chapters": novel["latest_chapter"],
                        "current_chapters": current_chapters,
                        "has_update": has_update,
                        "added": added,
                        "error": None
                    })
                except Exception as e:
                    results.append({
                        "id": novel["id"],
                        "title": novel["title"],
                        "url": novel["url"],
                        "error": str(e)
                    })

                progress_bar.progress((i + 1) / total_novels)
                time.sleep(1)

            progress_bar.empty()
            status_text.empty()
            
            # 結果をセッションに保存して画面をリロード
            st.session_state.check_results = results
            st.rerun()
        return

    # 【状態2】すでにチェックが終わっている場合、結果を並べるだけ（高速）
    st.subheader("更新チェック結果")
    
    for res in st.session_state.check_results:
        if res.get("error"):
            st.error(f"❌ **{res['title']}**: チェック失敗 - {res['error']}")
        else:
            if res["has_update"]:
                st.success(f"📈 **{res['title']}**: {res['saved_chapters']}話 → {res['current_chapters']}話 (+{res['added']}話)")
                
                # ここでボタンを押しても、手前の小説の time.sleep は絶対に走りません
                if st.button("📥 この作品の管理・DL画面へ", key=f"go_dl_{res['id']}"):
                    st.session_state.active_url = res["url"]
                    st.session_state.checked_latest_total = res["current_chapters"]
                    st.session_state.current_page = "download_and_manage"
                    st.rerun()
            else:
                st.info(f"✅ **{res['title']}**: 更新なし ({res['current_chapters']}話)")


# ページルーティング表示
if st.session_state.user_email is None:
    login_page()
elif st.session_state.current_page == "login":
    login_page()
elif st.session_state.current_page == "dashboard":
    dashboard_page()
elif st.session_state.current_page == "download_and_manage":
    download_and_manage_page()
elif st.session_state.current_page == "settings":
    settings_page()
elif st.session_state.current_page == "update_check":
    update_check_page()