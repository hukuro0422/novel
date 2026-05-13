import os
import re
import json
import time
import tempfile
import requests

from bs4 import BeautifulSoup
from ebooklib import epub
from urllib.parse import urlparse, urljoin


def normalize_narou_url(url):
    parsed = urlparse(url)
    novel_code = parsed.path.strip("/").split("/")[0]
    return f"{parsed.scheme}://{parsed.netloc}/{novel_code}/"


def normalize_kakuyomu_url(url):
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")

    if len(parts) >= 2 and parts[0] == "works":
        work_id = parts[1]
        return f"{parsed.scheme}://{parsed.netloc}/works/{work_id}"

    return url


def clean_filename(text):
    return re.sub(r'[\\/:*?"<>|]', '', text)


def detect_site(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if "syosetu.com" in host:
        return "narou"

    if "kakuyomu.jp" in host and "/works/" in path:
        return "kakuyomu"

    return "unknown"


def create_session():
    session = requests.Session()

    session.headers.update({
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Cookie": "over18=yes"
    })

    return session


def get_soup(session, url, log_callback=None):
    try:
        res = session.get(url, timeout=15)

        if res.status_code == 404:
            if log_callback:
                log_callback("作品が存在しません")
            return None

        res.raise_for_status()

        return BeautifulSoup(res.content, "html.parser")

    except Exception as e:
        if log_callback:
            log_callback(f"【通信エラー】: {url} - {e}")

        return None


def save_epub(
    main_title,
    vol_title,
    vol_episodes,
    file_idx,
    folder_path,
    site_name,
    cover_path=None
):
    if not vol_episodes:
        return

    safe_vol_title = clean_filename(vol_title)

    filename = f"{file_idx:02d}_{safe_vol_title}.epub"

    path = os.path.join(folder_path, filename)

    book = epub.EpubBook()

    book.set_title(f"{main_title} - {vol_title}")
    book.set_language("ja")
    book.add_author(site_name)

    style = """
    body {
        font-family: serif;
        padding: 1em;
        line-height: 1.8;
    }

    h1 {
        text-align: center;
    }

    p {
        text-indent: 1em;
        margin: 0.5em 0;
    }
    """

    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=style
    )

    book.add_item(css)

    if cover_path and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            book.set_cover(
                "cover" + os.path.splitext(cover_path)[1],
                f.read()
            )

    chapters = []

    for ep in vol_episodes:
        c = epub.EpubHtml(
            title=ep["title"],
            file_name=f"ep_{ep['id']}.xhtml"
        )

        c.set_content(
            f"""
            <html>
            <body>
            <h1>{ep["title"]}</h1>
            {ep["body"]}
            </body>
            </html>
            """
        )

        c.add_item(css)

        book.add_item(c)

        chapters.append(c)

    book.toc = tuple(chapters)

    book.spine = ['nav'] + chapters

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(path, book)

    return path


def create_epub(
    url,
    cover_path=None,
    progress_callback=None,
    log_callback=None
    start_episode=1
):
    session = create_session()

    site_type = detect_site(url)

    if site_type == "narou":
        top_url = normalize_narou_url(url)

    elif site_type == "kakuyomu":
        top_url = normalize_kakuyomu_url(url)

    else:
        raise Exception("対応していないURL")

    # デフォルト表紙

    if not cover_path:

        base_dir = os.path.dirname(os.path.abspath(__file__))

        default_cover = os.path.join(
            base_dir,
            "images",
            f"default_{site_type}.jpg"
        )

        if os.path.exists(default_cover):
            cover_path = default_cover

    output_dir = tempfile.mkdtemp()

    # =========================================================
    # カクヨム
    # =========================================================

    if site_type == "kakuyomu":

        if log_callback:
            log_callback("カクヨム解析開始")

        soup = get_soup(session, top_url, log_callback)

        if not soup:
            raise Exception("作品取得失敗")

        work_id = top_url.rstrip("/").split("/")[-1]

        next_data_script = soup.find(
            "script",
            id="__NEXT_DATA__"
        )

        if not next_data_script:
            raise Exception("JSONが見つかりません")

        data = json.loads(next_data_script.string)

        apollo = data.get(
            "props",
            {}
        ).get(
            "pageProps",
            {}
        ).get(
            "__APOLLO_STATE__",
            {}
        )

        def resolve(obj):
            if isinstance(obj, dict) and "__ref" in obj:
                return apollo.get(obj["__ref"])

            return obj

        work_data = resolve({
            "__ref": f"Work:{work_id}"
        })

        raw_title = work_data.get("title", "Novel")

        work_title = clean_filename(raw_title)

        book_folder = os.path.join(
            output_dir,
            work_title
        )

        os.makedirs(book_folder, exist_ok=True)

        final_structure = []

        all_chapters = {
            k: v for k, v in apollo.items()
            if v.get("__typename") == "Chapter"
        }

        all_toc_chapters = {
            k: v for k, v in apollo.items()
            if v.get("__typename") == "TableOfContentsChapter"
        }

        for key in apollo.keys():

            if key not in all_toc_chapters:
                continue

            toc_item = all_toc_chapters[key]

            chapter_obj = toc_item.get("chapter")

            if chapter_obj and "__ref" in chapter_obj:

                chapter_ref = chapter_obj["__ref"]

                if chapter_ref in all_chapters:

                    chapter_title = all_chapters[
                        chapter_ref
                    ].get(
                        "title",
                        "無題の章"
                    )

                    final_structure.append({
                        "type": "chapter",
                        "title": chapter_title
                    })

            for union_ref in (toc_item.get("episodeUnions") or []):

                episode = resolve(union_ref)

                if (
                    episode and
                    episode.get("__typename") == "Episode"
                ):
                    final_structure.append({
                        "type": "episode",
                        "id": episode.get("id"),
                        "title": episode.get("title") or "無題"
                    })

        episode_list = [
            s for s in final_structure
            if s["type"] == "episode"
        ]

        ep_content_map = {}

        for i, item in enumerate(episode_list, 1):

            ep_url = (
                f"https://kakuyomu.jp/works/"
                f"{work_id}/episodes/{item['id']}"
            )

            ep_soup = get_soup(
                session,
                ep_url,
                log_callback
            )

            if ep_soup:

                body = (
                    ep_soup.select_one(".widget-episodeBody")
                    or
                    ep_soup.select_one(".js-episode-body")
                )

                if body:

                    for t in body.find_all(["rt", "rp"]):
                        t.decompose()

                    for r in body.find_all("ruby"):
                        r.unwrap()

                    content = "".join([
                        f"<p>{p.get_text(strip=True)}</p>"
                        for p in body.find_all("p")
                        if p.get_text(strip=True)
                    ])

                    ep_content_map[item["id"]] = content

                    if progress_callback:
                        progress_callback(
                            i,
                            i / len(episode_list) * 100,
                            item["title"]
                        )

            time.sleep(0.1)

        curr_chapter = work_title

        buffer = []

        file_idx = 1

        for item in final_structure:

            if item["type"] == "chapter":

                if buffer:

                    save_epub(
                        work_title,
                        curr_chapter,
                        buffer,
                        file_idx,
                        book_folder,
                        "カクヨム",
                        cover_path
                    )

                    file_idx += 1

                    buffer = []

                curr_chapter = item["title"]

            elif (
                item["type"] == "episode"
                and item["id"] in ep_content_map
            ):

                buffer.append({
                    "id": item["id"],
                    "title": item["title"],
                    "body": ep_content_map[item["id"]]
                })

        if buffer:

            save_epub(
                work_title,
                curr_chapter,
                buffer,
                file_idx,
                book_folder,
                "カクヨム",
                cover_path
            )

        return book_folder

    # =========================================================
    # なろう
    # =========================================================

    elif site_type == "narou":

        if log_callback:
            log_callback("小説家になろう解析開始")

        structure = []

        work_title = ""

        current_idx = top_url

        visited = set()

        seen_ids = set()

        while current_idx:

            if current_idx in visited:
                break

            visited.add(current_idx)

            soup = get_soup(
                session,
                current_idx,
                log_callback
            )

            if not soup:
                break

            if not work_title:

                work_title = clean_filename(
                    soup.select_one(
                        ".p-novel__title"
                    ).get_text(strip=True)
                )

                book_folder = os.path.join(
                    output_dir,
                    work_title
                )

                os.makedirs(book_folder, exist_ok=True)

            index_box = soup.select_one(".p-eplist")

            if index_box:

                for child in index_box.find_all(
                    "div",
                    recursive=False
                ):

                    cls = child.get("class", [])

                    if "p-eplist__chapter-title" in cls:

                        structure.append({
                            "type": "chapter",
                            "title": child.get_text(strip=True)
                        })

                    elif "p-eplist__sublist" in cls:

                        subtitle_tag = child.select_one(
                            ".p-eplist__subtitle"
                        )

                        if subtitle_tag:

                            href = subtitle_tag["href"].rstrip("/")

                            eid = href.split("/")[-1]

                            if eid not in seen_ids:

                                seen_ids.add(eid)

                                structure.append({
                                    "type": "episode",
                                    "id": eid,
                                    "title": subtitle_tag.get_text(strip=True)
                                })

            next_link = soup.find(
                "a",
                string=lambda s: s and "次へ" in s
            )

            if next_link:

                current_idx = urljoin(
                    current_idx,
                    next_link["href"]
                )

                time.sleep(0.2)

            else:
                current_idx = None

        ep_list = [
            s for s in structure
            if s["type"] == "episode"
        ]

        ep_content_map = {}

        for i, item in enumerate(ep_list, 1):

            parsed = urlparse(top_url)

            novel_code = parsed.path.strip("/").split("/")[0]

            base = (
                f"{parsed.scheme}://"
                f"{parsed.netloc}/{novel_code}"
            )

            ep_url = f"{base}/{item['id']}/"

            ep_soup = get_soup(
                session,
                ep_url,
                log_callback
            )

            if ep_soup:

                blocks = ep_soup.find_all(
                    "div",
                    class_="js-novel-text"
                )

                html = ""

                for b in blocks:

                    if (
                        "p-novel__text--preface" in b.get("class", [])
                        or
                        "p-novel__text--afterword" in b.get("class", [])
                    ):
                        continue

                    for t in b.find_all(["rt", "rp"]):
                        t.decompose()

                    for r in b.find_all("ruby"):
                        r.unwrap()

                    html += "".join([
                        f"<p>{p.get_text(strip=True)}</p>"
                        for p in b.find_all("p")
                        if p.get_text(strip=True)
                    ])

                ep_content_map[item["id"]] = html

                if progress_callback:
                    progress_callback(
                        i,
                        i / len(ep_list) * 100,
                        item["title"]
                    )

            time.sleep(0.2)

        curr_vol = work_title

        buffer = []

        file_idx = 1

        for item in structure:

            if item["type"] == "chapter":

                if buffer:

                    save_epub(
                        work_title,
                        curr_vol,
                        buffer,
                        file_idx,
                        book_folder,
                        "小説家になろう",
                        cover_path
                    )

                    file_idx += 1

                    buffer = []

                curr_vol = item["title"]

            elif (
                item["type"] == "episode"
                and item["id"] in ep_content_map
            ):

                buffer.append({
                    "id": item["id"],
                    "title": item["title"],
                    "body": ep_content_map[item["id"]]
                })

        if buffer:

            save_epub(
                work_title,
                curr_vol,
                buffer,
                file_idx,
                book_folder,
                "小説家になろう",
                cover_path
            )

    return book_folder


def _get_narou_episode_count_from_top_page(soup, top_url):
    """
    小説家になろうの全話数を取得する。
    目次が複数ページに分かれていてもすべて数える。
    """
    if not soup:
        return 0

    session = create_session()
    episode_urls = set()
    current_url = top_url
    visited = set()

    while current_url:
        if current_url in visited:
            break
        visited.add(current_url)

        soup = get_soup(session, current_url)
        if not soup:
            break

        # 現在のページ内の話数を取得
        for a in soup.select(
            ".p-eplist__sublist > a, "
            ".p-eplist__sublist .p-eplist__subtitle"
        ):
            href = a.get("href")
            if href:
                full_url = urljoin(current_url, href)
                episode_urls.add(full_url)

        # 次ページリンクを探す
        next_link = soup.find(
            "a",
            string=lambda s: s and "次へ" in s
        )

        if next_link:
            current_url = urljoin(current_url, next_link["href"])
            time.sleep(0.2)
        else:
            current_url = None

    return len(episode_urls)


def _get_kakuyomu_internal_work_id(url, session, log_callback=None):
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) < 2 or path_parts[0] != "works":
        return None

    work_id = path_parts[1]
    search_url = f"https://kakuyomu.jp/search?work_id={work_id}"

    if log_callback:
        log_callback("カクヨムの作品IDを検索中")

    res = session.get(search_url, timeout=15)
    if res.status_code != 200:
        return None

    soup = BeautifulSoup(res.content, "html.parser")
    internal_ids = []
    for a in soup.select('a[href^="/works/"]'):
        href = a.get("href")
        if not href:
            continue
        parts = href.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "works":
            internal_id = parts[1]
            if internal_id not in internal_ids:
                internal_ids.append(internal_id)

    return internal_ids[0] if internal_ids else None


def _get_kakuyomu_episode_count_from_graphql(internal_id, session, log_callback=None):
    if not internal_id:
        return 0

    if log_callback:
        log_callback("カクヨムの全話数を取得中")

    query = '''
    query GetWorks($ids: [ID!]!) {
      works(ids: $ids) {
        id
        title
        publicEpisodeCount
      }
    }
    '''

    try:
        res = session.post(
            "https://kakuyomu.jp/graphql",
            json={"query": query, "variables": {"ids": [internal_id]}},
            timeout=15,
            headers={"Content-Type": "application/json"}
        )
        res.raise_for_status()
        data = res.json()
    except Exception:
        return 0

    works = data.get("data", {}).get("works")
    if not works:
        return 0

    work = works[0]
    return int(work.get("publicEpisodeCount", 0) or 0)


def get_latest_chapter_count(url: str, log_callback=None) -> int:
    """
    指定されたURLの小説の最新章数を取得する
    """
    session = create_session()
    site_type = detect_site(url)

    if site_type == "narou":
        top_url = normalize_narou_url(url)

        if log_callback:
            log_callback("小説家になろうの最新章数を取得中")

        soup = get_soup(session, top_url, log_callback)
        count = _get_narou_episode_count_from_top_page(soup, top_url)
        if count > 0:
            return count

        structure = []
        current_idx = top_url
        visited = set()
        seen_ids = set()

        while current_idx:
            if current_idx in visited:
                break

            visited.add(current_idx)
            soup = get_soup(session, current_idx, log_callback)

            if not soup:
                break

            # 目次取得
            toc = soup.select_one("#novel_honbun")
            if toc:
                for a in toc.find_all("a"):
                    href = a.get("href")
                    if href and "/n" in href:
                        full_url = urljoin(current_idx, href)
                        if full_url not in seen_ids:
                            seen_ids.add(full_url)
                            structure.append({
                                "url": full_url,
                                "title": a.get_text(strip=True)
                            })

            # 次ページ
            next_link = soup.select_one("a[title='次へ']")
            if next_link:
                current_idx = urljoin(current_idx, next_link.get("href"))
            else:
                current_idx = None

        return len(structure)

    elif site_type == "kakuyomu":
        top_url = normalize_kakuyomu_url(url)

        internal_id = _get_kakuyomu_internal_work_id(top_url, session, log_callback)
        if internal_id:
            count = _get_kakuyomu_episode_count_from_graphql(internal_id, session, log_callback)
            if count > 0:
                return count

        if log_callback:
            log_callback("カクヨムの最新章数を取得中")

        soup = get_soup(session, top_url, log_callback)

        if not soup:
            return 0

        next_data_script = soup.find("script", id="__NEXT_DATA__")

        if not next_data_script:
            return 0

        data = json.loads(next_data_script.string)
        apollo = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})

        def resolve(obj):
            if isinstance(obj, dict) and "__ref" in obj:
                return apollo.get(obj["__ref"])
            return obj

        # 作品情報取得
        work_id = top_url.rstrip("/").split("/")[-1]
        work_key = f"Work:{work_id}"

        if work_key not in apollo:
            return 0

        work = apollo[work_key]

        # 目次構造取得
        toc_key = work.get("tableOfContents", {}).get("__ref")
        if not toc_key or toc_key not in apollo:
            return 0

        toc = apollo[toc_key]

        final_structure = []

        for item_ref in toc.get("items", []):
            item = resolve(item_ref)

            if item and item.get("__typename") == "TableOfContentsChapterItem":
                final_structure.append({
                    "type": "chapter",
                    "title": item.get("title", "")
                })

            for union_ref in (item.get("episodeUnions") or []):
                episode = resolve(union_ref)

                if episode and episode.get("__typename") == "Episode":
                    final_structure.append({
                        "type": "episode",
                        "id": episode.get("id"),
                        "title": episode.get("title") or "無題"
                    })

        episode_list = [s for s in final_structure if s["type"] == "episode"]
        return len(episode_list)

    else:
        return 0