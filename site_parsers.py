"""各小説サイトのHTMLから本文を抽出する純粋関数。"""

from __future__ import annotations

import html
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def _container_to_paragraphs(container: Tag | None) -> str:
    if container is None:
        return ""

    # ルビは読み仮名を重複させず、本文側の文字だけを残す。
    for annotation in container.find_all(["rt", "rp"]):
        annotation.decompose()
    for ruby in container.find_all("ruby"):
        ruby.unwrap()

    paragraphs = []
    for paragraph in container.find_all("p"):
        text = paragraph.get_text(strip=True)
        if text:
            paragraphs.append(f"<p>{html.escape(text)}</p>")
    return "".join(paragraphs)


def extract_kakuyomu_body(soup: BeautifulSoup) -> str:
    """カクヨムのエピソード本文を取得する。"""
    body = (
        soup.select_one(".widget-episodeBody")
        or soup.select_one(".js-episode-body")
    )
    return _container_to_paragraphs(body)


def extract_narou_body(soup: BeautifulSoup) -> str:
    """なろう本文を取得し、従来仕様どおり前書き・後書きは除外する。"""
    parts = []
    for block in soup.find_all("div", class_="js-novel-text"):
        classes = set(block.get("class", []))
        if {
            "p-novel__text--preface",
            "p-novel__text--afterword",
        } & classes:
            continue
        parts.append(_container_to_paragraphs(block))
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
    """複数形式のページャーから次の目次ページを見つける。"""
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
