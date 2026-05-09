import streamlit as st
import tempfile
import os
import zipfile
import time
from datetime import datetime
import base64
from io import BytesIO

from novel_downloader import create_epub
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

st.set_page_config(
    page_title="Novel Downloader",
    page_icon="📚",
    layout="wide"
)

# セッション初期化
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "current_page" not in st.session_state:
    st.session_state.current_page = "login"


def log(msg):
    if "log_area" in st.session_state:
        st.session_state.log_area.text(msg)


def progress(i, percent, title):
    if "progress_bar" in st.session_state:
        st.session_state.progress_bar.progress(int(percent))
        st.session_state.log_area.text(f"{i}話目取得中: {title}")


def login_page():
    """ログイン・登録ページ"""
    st.title("📚 Novel Downloader")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("新規登録")
        email_new = st.text_input("メールアドレス（新規）", key="email_new")
        if st.button("登録"):
            if email_new:
                success, msg = register_user(email_new)
                if success:
                    st.success(msg)
                    st.session_state.user_email = email_new
                    st.session_state.current_page = "dashboard"
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.error("メールアドレスを入力してください")
    
    with col2:
        st.subheader("ログイン")
        email_login = st.text_input("メールアドレス", key="email_login")
        if st.button("ログイン"):
            if email_login:
                user = get_user_by_email(email_login)
                if user:
                    st.success("ログインしました")
                    st.session_state.user_email = email_login
                    st.session_state.current_page = "dashboard"
                    st.rerun()
                else:
                    st.error("このメールアドレスは登録されていません")
            else:
                st.error("メールアドレスを入力してください")


def dashboard_page():
    """ダッシュボード"""
    st.title("📚 ダッシュボード")
    st.write(f"ログイン中: {st.session_state.user_email}")
    
    if st.button("ログアウト"):
        st.session_state.user_email = None
        st.session_state.current_page = "login"
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
            st.caption(f"最新話数: {novel['latest_chapter']} 話")
            if novel['registered_at']:
                st.caption(f"登録日: {novel['registered_at'][:10]}")
        
        with col2:
            if st.button("📥 ダウンロード", key=f"download_{novel['id']}"):
                st.session_state.current_page = "download"
                st.session_state.selected_novel = novel
                st.rerun()
        
        with col3:
            if st.button("🗑️ 削除", key=f"delete_{novel['id']}"):
                delete_novel(novel['id'])
                st.success("削除しました")
                st.rerun()
        
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
                    # 最新話数を更新
                    epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
                    update_latest_chapter(novel_id, len(epub_files))
                    
                    st.success("小説を登録しました！")
                    
                    # ZIPダウンロード
                    zip_path = os.path.join(output_folder, f"{work_title}.zip")
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                        for epub_file in epub_files:
                            full_path = os.path.join(output_folder, epub_file)
                            zipf.write(full_path, arcname=epub_file)
                    
                    with open(zip_path, "rb") as f:
                        st.download_button(
                            label="📥 ZIPをダウンロード",
                            data=f,
                            file_name=f"{work_title}.zip",
                            mime="application/zip"
                        )
                    
                    # ダウンロード履歴を記録
                    record_download(
                        st.session_state.user_email,
                        novel_id,
                        len(epub_files),
                        zip_path
                    )
                    
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
    st.write(f"**最新話数:** {novel['latest_chapter']} 話")
    
    if st.button("📖 ダウンロード開始"):
        try:
            with st.spinner("EPUB生成中..."):
                st.session_state.progress_bar = st.progress(0)
                st.session_state.log_area = st.empty()
                
                # 表紙画像をファイルに保存
                cover_path = None
                if novel.get('cover_image'):
                    cover_bytes = base64.b64decode(novel['cover_image'])
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                    tmp.write(cover_bytes)
                    cover_path = tmp.name
                
                output_folder = create_epub(
                    novel['url'],
                    cover_path=cover_path,
                    progress_callback=progress,
                    log_callback=log
                )
            
            work_title = os.path.basename(output_folder)
            
            # 最新話数を更新
            epub_files = [f for f in os.listdir(output_folder) if f.endswith(".epub")]
            new_chapter_count = len(epub_files)
            old_chapter_count = novel.get('latest_chapter', 0)
            
            update_latest_chapter(novel['id'], new_chapter_count)
            
            # ZIPファイルを作成
            zip_path = os.path.join(output_folder, f"{work_title}.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for epub_file in epub_files:
                    full_path = os.path.join(output_folder, epub_file)
                    zipf.write(full_path, arcname=epub_file)
            
            with open(zip_path, "rb") as f:
                st.download_button(
                    label="📥 ZIPをダウンロード",
                    data=f,
                    file_name=f"{work_title}.zip",
                    mime="application/zip"
                )
            
            # ダウンロード履歴を記録
            record_download(
                st.session_state.user_email,
                novel['id'],
                new_chapter_count,
                zip_path
            )
            
            if new_chapter_count > old_chapter_count:
                st.success(f"✅ {new_chapter_count - old_chapter_count}話新しい話が追加されていました！")
            else:
                st.info("新しい話はまだアップロードされていません")
            
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

    if st.button("← ダッシュボードに戻る"):
        st.session_state.current_page = "dashboard"
        st.rerun()

    st.divider()

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
                # 更新あり
                st.success(f"📈 **{novel['title']}**: {novel['latest_chapter']}話 → {current_chapters}話 (+{current_chapters - novel['latest_chapter']}話)")

                # データベースを更新
                update_latest_chapter(novel['id'], current_chapters)
                updated_count += 1
            else:
                # 更新なし
                st.info(f"✅ **{novel['title']}**: 更新なし ({novel['latest_chapter']}話)")

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
    
    novels = get_user_novels(st.session_state.user_email)
    
    if not novels:
        st.info("登録済み小説がありません")
        return
    
    selected_novel = st.selectbox(
        "小説を選択",
        novels,
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
