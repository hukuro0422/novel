import streamlit as st
import tempfile
import os
import zipfile

from novel_downloader import create_epub


st.set_page_config(
    page_title="Novel Downloader",
    page_icon="📚",
    layout="centered"
)

st.title("📚 Novel Downloader")

url = st.text_input(
    "小説URL",
    placeholder="https://ncode.syosetu.com/... or https://kakuyomu.jp/works/..."
)

cover = st.file_uploader(
    "カバー画像",
    type=["jpg", "jpeg", "bmp"]
)

progress_bar = st.progress(0)

log_area = st.empty()


def log(msg):
    log_area.text(msg)


def progress(i, percent, title):
    progress_bar.progress(int(percent))
    log_area.text(f"{i}話目取得中: {title}")


if st.button("ダウンロード開始"):

    if not url.strip():
        st.error("URLを入力してください")

    else:

        cover_path = None

        if cover:

            suffix = os.path.splitext(cover.name)[1]

            tmp = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            )

            tmp.write(cover.read())

            cover_path = tmp.name

        try:

            with st.spinner("EPUB生成中..."):

                output_folder = create_epub(
                    url,
                    cover_path=cover_path,
                    progress_callback=progress,
                    log_callback=log
                )

            epub_files = [
                f for f in os.listdir(output_folder)
                if f.endswith(".epub")
            ]

            st.success("生成完了")

            work_title = os.path.basename(output_folder)

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

            with open(zip_path, "rb") as f:

                st.download_button(
                    label="📥 ZIPをダウンロード",
                    data=f,
                    file_name=f"{work_title}.zip",
                    mime="application/zip"
                )

        except Exception as e:

            st.error(str(e))