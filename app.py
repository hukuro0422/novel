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
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager
import warnings

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
cookies = EncryptedCookieManager(
    prefix="novel_downloader/",
    password=st.secrets.get(
        "COOKIE_PASSWORD",
        "novel-downloader-local-fallback-key-2026"
    )
)


# Cookie の準備が完了するまで待つ
if not cookies.ready():
    st.info("ブラウザ設定を読み込み中です...")
    st.stop()

# ===== デバッグ表示（Cookie の状態確認）=====
with st.expander("🔍 Cookie デバッグ情報"):
    st.write("cookies.ready() =", cookies.ready())
    st.write("保存されている user_email =", cookies.get("user_email"))
    st.write("ブラウザが Cookie を受け入れていない場合、この値は None になります。")


# セッション初期化
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "current_page" not in st.session_state:
    st.session_state.current_page = "login"

if st.session_state.user_email is None:
    saved_email = cookies.get("user_email")
    if saved_email:
        user = get_user_by_email(saved_email)
        if user:
            st.session_state.user_email = saved_email
            st.session_state.current_page = "dashboard"


def log(msg):
    if "log_area" in st.session_state:
        st.session_state.log_area.text(msg)


def progress(i, percent, title):
    if "progress_bar" in st.session_state:
        # 0〜100の範囲に制限
        st.session_state.progress_bar.progress(min(int(percent), 100))

        # 現在の進捗を表示
        st.session_state.log_area.text(
            f"{i}話目取得中 ({percent:.1f}%): {title}"
        )


def login_page():
    """ログイン・登録ページ"""
    st.title("📚 Novel Downloader")

    # Cookie の確認（デバッグ用）
    st.write("Saved cookie:", cookies.get("user_email"))

    # ===== ログインを上に配置 =====
    st.subheader("ログイン")

    # form を使うと Enter / キーボードの「決定」で送信できる
    with st.form("login_form"):
        email_login = st.text_input(
            "メールアドレス",
            key="email_login"
        )
        login_submitted = st.form_submit_button("ログイン")

    if login_submitted:
        if email_login:
            user = get_user_by_email(email_login)

            if user:
                st.success("ログインしました")

                # Cookie に保存
                cookies["user_email"] = email_login
                cookies.save()

                # セッションに保存
                st.session_state.user_email = email_login
                st.session_state.current_page = "dashboard"

                st.rerun()
            else:
                st.error("このメールアドレスは登録されていません")
        else:
            st.error("メールアドレスを入力してください")

    st.divider()

    # ===== 新規登録を下に配置 =====
    st.subheader("新規登録")

    with st.form("register_form"):
        email_new = st.text_input(
            "メールアドレス（新規）",
            key="email_new"
        )
        register_submitted = st.form_submit_button("登録")

    if register_submitted:
        if email_new:
            success, msg = register_user(email_new)

            if success:
                st.success(msg)

                # Cookie に保存
                cookies["user_email"] = email_new
                cookies.save()

                # セッションに保存
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
        # セッションをクリア
        st.session_state.user_email = None
        st.session_state.current_page = "login"

        # Cookie を確実に削除
        cookies["user_email"] = ""
        cookies.save()

        # 選択中の小説も削除
        if "selected_novel" in st.session_state:
            del st.session_state["selected_novel"]

        st.success("ログアウトしました")
        st.rerun()
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📖 新規小説を追加"):
            st.session_state.current_page = "add_novel"
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
    
    # 登録済み小説一覧
    st.subheader("登録済み小説")
    novels = get_user_novels(st.session_state.user_email)
    
    if not novels:
        st.info("登録済み小説がありません")
        return
    
    for novel in novels:
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            st.write(f"**{novel['title']}**")
            st.caption(f"URL: {novel['url']}")
            # 毎回最新の全話数を取得して表示
            #current_total = get_latest_chapter_count(novel['url'])
            #st.caption(f"全話数: {current_total} 話")
            if novel.get('last_downloaded_at'):
                st.caption(f"更新日: {novel['last_downloaded_at'][:10]}")
            elif novel.get('registered_at'):
                st.caption(f"更新日: {novel['registered_at'][:10]}")
        
        with col2:
            if st.button("📥 ダウンロード", key=f"download_{novel['id']}"):
                st.session_state.current_page = "download"
                st.session_state.selected_novel = novel
                st.rerun()
        
        with col3:
            if st.button("🗑️ 削除", key=f"delete_novel_{novel['id']}"):
                try:
                    success = delete_novel(novel['id'])

                    if success:
                        st.success(f"「{novel['title']}」を削除しました。")

                        # ダウンロード画面で選択中だった場合は解除
                        if (
                            "selected_novel" in st.session_state
                            and st.session_state.selected_novel.get("id") == novel["id"]
                        ):
                            del st.session_state["selected_novel"]

                        # ダッシュボードを再読み込み
                        st.session_state.current_page = "dashboard"
                        st.rerun()
                    else:
                        st.error("削除に失敗しました。もう一度お試しください。")

                except Exception as e:
                    st.error(f"削除中にエラーが発生しました: {e}")
        
        st.divider()


def add_novel_page():
    """小説追加ページ"""
    st.title("📖 新規小説を追加")
    
    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()
    
    st.divider()
    
    url = st.text_input(
        "小説URL",
        placeholder="https://ncode.syosetu.com/... or https://kakuyomu.jp/works/..."
    )
    
    cover = st.file_uploader(
        "カバー画像（オプション）",
        type=["jpg", "jpeg", "bmp"]
    )
    
    if st.button("登録して最初のダウンロード"):
        if not url.strip():
            st.error("URLを入力してください")
        else:
            cover_path = None
            if cover:
                suffix = os.path.splitext(cover.name)[1]
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(cover.read())
                cover_path = tmp.name
            
            try:
                with st.spinner("EPUB生成中..."):
                    st.session_state.progress_bar = st.progress(0)
                    st.session_state.log_area = st.empty()
                    
                    output_folder = create_epub(
                        url,
                        cover_path=cover_path,
                        progress_callback=progress,
                        log_callback=log
                    )
                
                work_title = os.path.basename(output_folder)
                
                # 小説を登録
                success, msg, novel_id = register_novel(
                    st.session_state.user_email,
                    url,
                    work_title,
                    cover
                )
                
                if success:
                    epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
                    actual_total = get_latest_chapter_count(url)
                    update_latest_chapter(novel_id, actual_total)
                    
                    st.success("小説を登録しました！")
                    
                    # ZIPダウンロード
                    zip_path = os.path.join(output_folder, f"{work_title}.zip")
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                        for epub_file in epub_files:
                            full_path = os.path.join(output_folder, epub_file)
                            zipf.write(full_path, arcname=epub_file)
                    
                    with open(zip_path, "rb") as f:
                        downloaded = st.download_button(
                            label="📥 ZIPをダウンロード",
                            data=f.read(),
                            file_name=f"{work_title}.zip",
                            mime="application/zip"
                        )
                    
                    if downloaded:
                        # 実際にボタンが押された時だけ記録
                        record_download(
                            st.session_state.user_email,
                            novel_id,
                            actual_total,
                            zip_path
                        )

                        update_latest_chapter(novel_id, actual_total)

                        st.success("ダウンロード履歴を更新しました。")
                    
                    st.info("ダッシュボードでいつでもダウンロードできます")
                else:
                    st.error(msg)
                
            except Exception as e:
                st.error(f"エラー: {str(e)}")


def download_page():
    """ダウンロードページ"""
    if "selected_novel" not in st.session_state:
        st.error("エラー：小説が選択されていません")
        return

    novel = st.session_state.selected_novel
    st.title(f"📥 {novel['title']} をダウンロード")

    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()

    st.divider()

    # 表紙画像を表示
    if novel.get('cover_image'):
        st.subheader("表紙")
        cover_bytes = base64.b64decode(novel['cover_image'])
        st.image(cover_bytes, width=200)

    st.write(f"**URL:** {novel['url']}")

    # 現在の全話数を取得
    current_total = get_latest_chapter_count(novel['url'])
    st.write(f"**全話数:** {current_total} 話")

    # 前回保存時の話数
    saved_total = novel.get('latest_chapter', 0)

    # 更新がある場合のみメッセージ表示
    if current_total > saved_total:
        added = current_total - saved_total
        st.success(f"📈 {added}話の更新があります")
    else:
        st.info("最新の状態です")

    # ダウンロード方法の選択
    download_mode = st.radio(
        "ダウンロード方法",
        ["更新分のみ", "全話"],
        horizontal=True
    )

    # 「更新分のみ」を選んだときに更新がなければボタンを無効化
    no_update = current_total <= saved_total
    disable_download = (
        download_mode == "更新分のみ" and no_update
    )

    if disable_download:
        st.warning("更新がないため「更新分のみ」はダウンロードできません。")

    if st.button(
        "📖 ダウンロード開始",
        disabled=disable_download
    ):
        try:
            with st.spinner("EPUB生成中..."):
                st.session_state.progress_bar = st.progress(0)
                st.session_state.log_area = st.empty()

                # 表紙画像を一時保存
                cover_path = None
                if novel.get('cover_image'):
                    cover_bytes = base64.b64decode(novel['cover_image'])
                    tmp = tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix='.jpg'
                    )
                    tmp.write(cover_bytes)
                    tmp.close()
                    cover_path = tmp.name

                # ダウンロード開始話数を決定
                if download_mode == "更新分のみ":
                    start_episode = saved_total + 1
                else:
                    start_episode = 1

                # EPUB生成
                output_folder = create_epub(
                    novel['url'],
                    cover_path=cover_path,
                    progress_callback=progress,
                    log_callback=log,
                    start_episode=start_episode
                )

            work_title = os.path.basename(output_folder)

            epub_files = [
                f for f in os.listdir(output_folder)
                if f.endswith(".epub")
            ]

            if not epub_files:
                st.error("EPUBファイルが生成されませんでした。")
                return

            # ZIP作成
            zip_path = os.path.join(
                output_folder,
                f"{work_title}.zip"
            )

            with zipfile.ZipFile(
                zip_path,
                "w",
                zipfile.ZIP_DEFLATED
            ) as zipf:
                for epub_file in epub_files:
                    full_path = os.path.join(
                        output_folder,
                        epub_file
                    )
                    zipf.write(
                        full_path,
                        arcname=epub_file
                    )

            
            # ZIP作成後すぐにDB更新
            actual_total = get_latest_chapter_count(novel['url'])

            record_download(
                st.session_state.user_email,
                novel['id'],
                actual_total,
                zip_path
            )

            update_latest_chapter(
                novel['id'],
                actual_total
            )

            # セッション上の値も更新
            novel['latest_chapter'] = actual_total

            # ダウンロードボタンを表示
            with open(zip_path, "rb") as f:
                st.download_button(
                    label="📥 ZIPをダウンロード",
                    data=f.read(),
                    file_name=f"{work_title}.zip",
                    mime="application/zip"
                )

            # 成功メッセージ
            if actual_total > saved_total:
                st.success(
                    f"✅ {actual_total - saved_total}話分を保存しました。"
                )
            else:
                st.success("✅ ダウンロード準備が完了しました。")


        except Exception as e:
            st.error(f"エラー: {str(e)}")


def settings_page():
    """設定ページ"""
    st.title("⚙️ 設定")
    
    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()
    
    st.divider()


def update_check_page():
    """全て更新チェックページ"""
    st.title("🔄 全て更新チェック")

    if "update_check_done" not in st.session_state:
        st.session_state.update_check_done = False

    if st.button("← ダッシュボードに戻る"):
        st.session_state.update_check_done = False
        st.session_state.current_page = "dashboard"
        st.rerun()

    st.divider()

    if not st.session_state.update_check_done:
        if st.button("🔍 更新チェック開始"):
            st.session_state.update_check_done = True
            st.rerun()
        return


    # 登録済み小説を取得
    novels = get_user_novels(st.session_state.user_email)

    if not novels:
        st.info("登録済み小説がありません")
        return

    st.subheader("更新チェック結果")

    # プログレスバー
    progress_bar = st.progress(0)
    status_text = st.empty()

    total_novels = len(novels)
    updated_count = 0

    for i, novel in enumerate(novels):
        status_text.text(f"チェック中: {novel['title']} ({i+1}/{total_novels})")

        try:
            # 最新章数を取得
            from novel_downloader import get_latest_chapter_count
            current_chapters = get_latest_chapter_count(novel['url'])
            
            if current_chapters > novel['latest_chapter']:
                added = current_chapters - novel['latest_chapter']
            
                st.success(
                    f"📈 **{novel['title']}**: "
                    f"{novel['latest_chapter']}話 → {current_chapters}話 "
                    f"(+{added}話)"
                )
            
                updated_count += 1
            
                # ダウンロード方法選択
                download_mode = st.radio(
                    "ダウンロード方法",
                    ["更新分のみ", "全話"],
                    key=f"mode_{novel['id']}",
                    horizontal=True
                )
            
                # 個別ダウンロードボタン
                if st.button(
                    "📥 この作品をダウンロード",
                    key=f"download_update_{novel['id']}"
                ):
                    st.session_state.selected_novel = novel
                    st.session_state.download_mode = download_mode
                    st.session_state.current_page = "download"
                    st.rerun()
            
            else:
                st.info(
                    f"✅ **{novel['title']}**: 更新なし "
                    f"({current_chapters}話)"
                )


        except Exception as e:
            st.error(f"❌ **{novel['title']}**: チェック失敗 - {str(e)}")

        # プログレス更新
        progress_bar.progress((i + 1) / total_novels)

        # 少し待機してサーバーに負荷をかけない
        time.sleep(1)

    progress_bar.empty()
    status_text.empty()

    if updated_count > 0:
        st.success(f"更新チェック完了！ {updated_count}件の小説が更新されました。")
    else:
        st.info("更新チェック完了！ 更新はありませんでした。")
    
    st.subheader("表紙画像の管理")
    
    cover_novels = get_user_novels(st.session_state.user_email)
    
    if not cover_novels:
        st.info("登録済み小説がありません")
        return
    
    selected_novel = st.selectbox(
        "小説を選択",
        cover_novels,
        format_func=lambda x: x['title']
    )
    
    st.write(f"**選択中:** {selected_novel['title']}")
    
    if selected_novel.get('cover_image'):
        st.subheader("現在の表紙")
        cover_bytes = base64.b64decode(selected_novel['cover_image'])
        st.image(cover_bytes, width=200)
    else:
        st.info("表紙がまだ設定されていません")
    
    st.subheader("表紙を変更")
    new_cover = st.file_uploader(
        "新しい表紙画像を選択",
        type=["jpg", "jpeg", "bmp"],
        key="new_cover"
    )
    
    if new_cover and st.button("表紙を更新"):
        if update_cover_image(selected_novel['id'], new_cover):
            st.success("表紙を更新しました！")
            st.rerun()
        else:
            st.error("表紙の更新に失敗しました")

    if updated_count > 0:
        st.divider()

        if st.button("📥 更新のある作品をすべてダウンロード"):
            try:
                with st.spinner("更新作品をまとめてダウンロード中..."):
                    temp_dir = tempfile.mkdtemp()
                    combined_zip_path = os.path.join(
                        temp_dir,
                        f"updated_novels_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                    )

                    updated_novels = []

                    for novel in novels:
                        current_chapters = get_latest_chapter_count(novel["url"])
                        if current_chapters > novel["latest_chapter"]:
                            updated_novels.append((novel, current_chapters))

                    with zipfile.ZipFile(
                        combined_zip_path,
                        "w",
                        zipfile.ZIP_DEFLATED
                    ) as combined_zip:

                        for idx, (novel, current_chapters) in enumerate(updated_novels):
                            status_text.text(
                                f"ダウンロード中: {novel['title']} "
                                f"({idx+1}/{len(updated_novels)})"
                            )

                            cover_path = None
                            if novel.get("cover_image"):
                                cover_bytes = base64.b64decode(novel["cover_image"])
                                tmp_cover = tempfile.NamedTemporaryFile(
                                    delete=False,
                                    suffix=".jpg"
                                )
                                tmp_cover.write(cover_bytes)
                                tmp_cover.close()
                                cover_path = tmp_cover.name

                            output_folder = create_epub(
                                novel["url"],
                                cover_path=cover_path,
                                progress_callback=progress,
                                log_callback=log
                            )

                            epub_files = [
                                f for f in os.listdir(output_folder)
                                if f.endswith(".epub")
                            ]

                            for epub_file in epub_files:
                                epub_path = os.path.join(output_folder, epub_file)
                                combined_zip.write(
                                    epub_path,
                                    arcname=epub_file
                                )

                    with open(combined_zip_path, "rb") as f:
                        downloaded = st.download_button(
                            label="📥 updated_novels.zip をダウンロード",
                            data=f.read(),
                            file_name=os.path.basename(combined_zip_path),
                            mime="application/zip"
                        )

                    if downloaded:
                        for novel, current_chapters in updated_novels:
                            record_download(
                                st.session_state.user_email,
                                novel["id"],
                                current_chapters,
                                combined_zip_path
                            )
                            update_latest_chapter(
                                novel["id"],
                                current_chapters
                            )

                        st.success(
                            f"{len(updated_novels)}作品をまとめてダウンロードしました。"
                        )
                        st.rerun()

            except Exception as e:
                st.error(f"一括ダウンロード中にエラーが発生しました: {e}")


# ページ表示
if st.session_state.user_email is None:
    login_page()
elif st.session_state.current_page == "login":
    login_page()
elif st.session_state.current_page == "dashboard":
    dashboard_page()
elif st.session_state.current_page == "add_novel":
    add_novel_page()
elif st.session_state.current_page == "download":
    download_page()
elif st.session_state.current_page == "settings":
    settings_page()
elif st.session_state.current_page == "update_check":
    update_check_page()
