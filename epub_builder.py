"""EPUB生成処理。サイト取得ロジックから独立させて検証可能にする。"""

from __future__ import annotations

import hashlib
import html
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ebooklib import epub

from image_processor import process_illustration_image


def _fetch_and_process_image(
    src: str,
    session: requests.Session | None = None,
    grayscale: bool = False,
) -> bytes | None:
    """挿絵画像をダウンロードまたはローカルから読み込み、JPEG最適化して返す。"""
    try:
        if os.path.exists(src):
            with open(src, "rb") as f:
                return process_illustration_image(f.read(), grayscale=grayscale)

        if src.startswith("http://") or src.startswith("https://"):
            client = session or requests
            res = client.get(src, timeout=15)
            if res.status_code == 200 and res.content:
                return process_illustration_image(res.content, grayscale=grayscale)
    except Exception as e:
        print(f"挿絵取得スキップ ({src}): {e}")
    return None


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(value: str, fallback: str = "novel") -> str:
    """Windowsでも安全な、長すぎないファイル名へ変換する。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:120].rstrip(". ") or fallback


def sanitize_body_html(body_html: str) -> str:
    """EPUB本文として必要な最小限の要素だけを残す。挿絵のimgタグはsrc/altを保持する。"""
    soup = BeautifulSoup(body_html or "", "html.parser")
    allowed_tags = {"p", "br", "em", "strong", "ruby", "rt", "rp", "img"}

    for tag in list(soup.find_all(True)):
        if tag.name in {"script", "style"}:
            tag.decompose()
            continue
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue
        if tag.name == "img":
            tag.attrs = {k: v for k, v in tag.attrs.items() if k in {"src", "alt"}}
        else:
            tag.attrs = {}

    return str(soup)


def write_epub(
    main_title: str,
    volume_title: str,
    episodes: list[dict],
    file_index: int,
    folder_path: str,
    site_name: str,
    cover_path: str | None = None,
    session: requests.Session | None = None,
    grayscale_illustrations: bool = False,
) -> str | None:
    """一つの章・巻をEPUBとして書き出す。"""
    if not episodes:
        return None

    safe_volume_title = safe_filename(volume_title, "本編")
    filename = f"{file_index:02d}_{safe_volume_title}.epub"
    output_path = Path(folder_path) / filename

    book = epub.EpubBook()
    identifier_source = f"{site_name}\0{main_title}\0{volume_title}"
    identifier = hashlib.sha256(identifier_source.encode("utf-8")).hexdigest()
    book.set_identifier(f"novel-downloader:{identifier}")
    book.set_title(f"{main_title} - {volume_title}")
    book.set_language("ja")
    book.add_author(site_name)
    book.add_metadata("DC", "source", site_name)

    style = """
    html { writing-mode: horizontal-tb; }
    body { font-family: serif; padding: 1em; line-height: 1.9; }
    h1 { text-align: center; line-height: 1.5; margin: 1.5em 0 2em; }
    p { text-indent: 1em; margin: 0.55em 0; }
    p.illustration { text-indent: 0; text-align: center; margin: 1.5em 0; }
    img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
    """
    css = epub.EpubItem(
        uid="book-style",
        file_name="styles/book.css",
        media_type="text/css",
        content=style,
    )
    book.add_item(css)

    if cover_path and os.path.exists(cover_path):
        extension = Path(cover_path).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png"}:
            extension = ".jpg"
        with open(cover_path, "rb") as cover_file:
            book.set_cover(f"cover{extension}", cover_file.read())

    image_cache: dict[str, str] = {}
    image_counter = 0

    chapters = []
    for order, episode in enumerate(episodes, 1):
        episode_id = safe_filename(str(episode.get("id") or order), str(order))
        episode_title = str(episode.get("title") or "無題")

        raw_body = sanitize_body_html(episode.get("body", ""))
        body_soup = BeautifulSoup(raw_body, "html.parser")

        for img_tag in body_soup.find_all("img"):
            src = img_tag.get("src", "")
            if not src:
                img_tag.decompose()
                continue

            if src not in image_cache:
                image_counter += 1
                img_bytes = _fetch_and_process_image(
                    src,
                    session=session,
                    grayscale=grayscale_illustrations,
                )
                if img_bytes:
                    img_file_name = f"images/illust_{image_counter:04d}.jpg"
                    epub_img = epub.EpubItem(
                        uid=f"illust-{image_counter}",
                        file_name=img_file_name,
                        media_type="image/jpeg",
                        content=img_bytes,
                    )
                    book.add_item(epub_img)
                    image_cache[src] = f"../{img_file_name}"
                else:
                    image_cache[src] = ""

            resolved_src = image_cache[src]
            if resolved_src:
                img_tag["src"] = resolved_src
            else:
                img_tag.decompose()

        cleaned_body = str(body_soup)

        chapter = epub.EpubHtml(
            uid=f"episode-{file_index}-{order}",
            title=episode_title,
            file_name=f"episodes/{order:05d}_{episode_id}.xhtml",
            lang="ja",
        )
        chapter.set_content(
            "<!doctype html>"
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="ja">'
            "<head>"
            f"<title>{html.escape(episode_title)}</title>"
            "</head><body>"
            f"<h1>{html.escape(episode_title)}</h1>"
            f"{cleaned_body}"
            "</body></html>"
        )
        chapter.add_item(css)
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = tuple(chapters)
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(output_path), book, {})
    return str(output_path)
