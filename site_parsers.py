"""各小説サイトのHTMLから本文を抽出する純粋関数。"""

from __future__ import annotations

import html
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def _clean_img_url(src: str, base_url: str = "") -> str:
    src = (src or "").strip()
    if not src:
        return ""
    if src.startswith("//"):
        return f"https:{src}"
    if base_url:
        return urljoin(base_url, src)
    return src


def _container_to_paragraphs(container: Tag | None, base_url: str = "") -> str:
    if container is None:
        return ""

    # ルビは読み仮名を重複させず、本文側の文字だけを残す。
    for annotation in container.find_all(["rt", "rp"]):
        annotation.decompose()
    for ruby in container.find_all("ruby"):
        ruby.unwrap()

    paragraphs = []
    processed_img_ids = set()

    for element in container.find_all(["p", "div", "figure", "img"]):
        if getattr(element, "attrs", None) is None:
            continue

        classes = set(element.get("class", []))
        if element.name == "div" and "novelview_image" not in classes:
            continue

        if element.name == "p":
            for img in element.find_all("img"):
                processed_img_ids.add(id(img))
                src = _clean_img_url(img.get("src", ""), base_url)
                if src:
                    paragraphs.append(f'<p class="illustration"><img src="{html.escape(src)}"/></p>')

            if element.find("img"):
                text = "".join(s.strip() for s in element.strings if getattr(s.parent, "name", None) != "img")
            else:
                text = element.get_text(strip=True)

            if text:
                paragraphs.append(f"<p>{html.escape(text)}</p>")

        elif element.name in ("div", "figure") and ("novelview_image" in classes or element.name == "figure"):
            for img in element.find_all("img"):
                if id(img) not in processed_img_ids:
                    processed_img_ids.add(id(img))
                    src = _clean_img_url(img.get("src", ""), base_url)
                    if src:
                        paragraphs.append(f'<p class="illustration"><img src="{html.escape(src)}"/></p>')

        elif element.name == "img":
            if id(element) not in processed_img_ids:
                processed_img_ids.add(id(element))
                src = _clean_img_url(element.get("src", ""), base_url)
                if src:
                    paragraphs.append(f'<p class="illustration"><img src="{html.escape(src)}"/></p>')

    return "".join(paragraphs)


def extract_kakuyomu_body(soup: BeautifulSoup, base_url: str = "") -> str:
    """カクヨムのエピソード本文を取得する。"""
    body = (
        soup.select_one(".widget-episodeBody")
        or soup.select_one(".js-episode-body")
    )
    return _container_to_paragraphs(body, base_url)


def extract_narou_body(soup: BeautifulSoup, base_url: str = "") -> str:
    """なろう本文を取得し、従来仕様どおり前書き・後書きは除外して本文と挿絵を抽出する。"""
    body_container = soup.select_one("#novel_honbun, .p-novel__body")
    if body_container:
        for preface in body_container.select(".p-novel__text--preface, .novel_p"):
            preface.decompose()
        for afterword in body_container.select(".p-novel__text--afterword, .novel_a"):
            afterword.decompose()
        return _container_to_paragraphs(body_container, base_url)

    parts = []
    for block in soup.find_all("div", class_=["js-novel-text", "novelview_image"]):
        classes = set(block.get("class", []))
        if {
            "p-novel__text--preface",
            "p-novel__text--afterword",
        } & classes:
            continue
        parts.append(_container_to_paragraphs(block, base_url))
    return "".join(parts)


def extract_narou_toc_entries(soup: BeautifulSoup) -> list[dict]:
    """なろうの目次1ページから章見出しと各話URLを話順に取得する。"""
    entries = []
    index_box = soup.select_one(".p-eplist")
    if index_box is None:
        return entries

    for child in index_box.find_all("div", recursive=False):
        classes = set(child.get("class", []))
        if "p-eplist__chapter-title" in classes:
            entries.append({
                "type": "chapter",
                "title": child.get_text(strip=True) or "無題の章",
            })
            continue

        if "p-eplist__sublist" not in classes:
            continue
        subtitle = child.select_one(".p-eplist__subtitle")
        if subtitle is None or not subtitle.get("href"):
            continue
        href = subtitle["href"]
        episode_id = href.rstrip("/").split("/")[-1]
        entries.append({
            "type": "episode",
            "id": episode_id,
            "title": subtitle.get_text(strip=True) or "無題",
            "href": href,
        })
    return entries


def find_narou_next_toc_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """バックアップ版の「次へ」判定を優先して次の目次ページを見つける。"""
    legacy_next = soup.find(
        "a",
        string=lambda value: value and "次へ" in value,
    )
    if legacy_next is not None and legacy_next.get("href"):
        return urljoin(current_url, legacy_next["href"])

    # 表示文字が子要素へ分割された場合だけ、現在形式の判定へフォールバックする。
    candidates = [
        soup.select_one('a[rel="next"]'),
        soup.select_one("a.c-pager__item--next"),
        soup.select_one(".c-pager a:last-of-type"),
    ]
    candidates.extend(
        anchor for anchor in soup.find_all("a")
        if "次へ" in anchor.get_text(" ", strip=True)
    )

    for anchor in candidates:
        if anchor is None or not anchor.get("href"):
            continue
        label = anchor.get_text(" ", strip=True)
        rel = anchor.get("rel", [])
        classes = set(anchor.get("class", []))
        if (
            "next" in rel
            or "c-pager__item--next" in classes
            or "次へ" in label
        ):
            return urljoin(current_url, anchor["href"])
    return None

