import os
import re
import json
import time
import tempfile
import requests

from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

from epub_builder import safe_filename, write_epub
from site_parsers import (
    extract_kakuyomu_body,
    extract_narou_body,
    extract_narou_toc_entries,
    find_narou_next_toc_url,
)

from networking import (
    AccessRestrictedError,
    NovelNetworkError,
    PoliteSession,
    WorkNotFoundError,
    validate_response,
)


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
    """旧コードとの互換用。新規処理はepub_builder.safe_filenameを使用する。"""
    return safe_filename(text)


def build_episode_cache(cached_episodes=None):
    """DB行を、サイト間で共通利用できる本文マップへ変換する。"""
    if isinstance(cached_episodes, dict):
        nested = cached_episodes.get("data")
        cached_episodes = (
            nested if isinstance(nested, (list, tuple)) else [cached_episodes]
        )
    return {
        str(ep["episode_id"]): ep.get("body_html", "")
        for ep in (cached_episodes or [])
        if isinstance(ep, dict)
        and ep.get("episode_id")
        and ep.get("body_html")
    }


def find_missing_episodes(episode_list, cached_content, start_episode=1):
    """指定位置以降から、本文が未取得の話だけを話順のまま返す。"""
    return [
        item for item in episode_list[max(start_episode - 1, 0):]
        if str(item["id"]) not in cached_content
    ]


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
    session = PoliteSession()

    session.headers.update({
        "User-Agent": "NovelDownloader/2.0 (personal EPUB reader)",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Cookie": "over18=yes"
    })

    return session


def get_soup(session, url, log_callback=None):
    try:
        res = session.get(url)
        validate_response(res, url)

        return BeautifulSoup(res.content, "html.parser")

    except (WorkNotFoundError, AccessRestrictedError):
        raise
    except NovelNetworkError:
        raise
    except requests.RequestException as e:
        if log_callback:
            log_callback(f"【通信エラー】: {url} - {e}")
        raise NovelNetworkError(f"通信に失敗しました: {url}") from e


def save_epub(
    main_title,
    vol_title,
    vol_episodes,
    file_idx,
    folder_path,
    site_name,
    cover_path=None
):
    return write_epub(
        main_title=main_title,
        volume_title=vol_title,
        episodes=vol_episodes,
        file_index=file_idx,
        folder_path=folder_path,
        site_name=site_name,
        cover_path=cover_path,
    )


def create_epub(
    url,
    cover_path=None,
    progress_callback=None,
    log_callback=None,
    start_episode=1,
    cached_episodes=None,
    chapter_callback=None,
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

        ep_content_map = build_episode_cache(cached_episodes)

        chapter_by_episode = {}
        current_chapter_title = work_title
        for structure_item in final_structure:
            if structure_item["type"] == "chapter":
                current_chapter_title = structure_item["title"]
            elif structure_item["type"] == "episode":
                chapter_by_episode[str(structure_item["id"])] = current_chapter_title

        missing_episodes = find_missing_episodes(episode_list, ep_content_map)

        for i, item in enumerate(missing_episodes, 1):

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

                content = extract_kakuyomu_body(ep_soup)

                if content:

                    episode_id = str(item["id"])
                    ep_content_map[episode_id] = content

                    if chapter_callback:
                        chapter_callback({
                            "episode_id": episode_id,
                            "episode_index": episode_list.index(item) + 1,
                            "chapter_title": chapter_by_episode.get(episode_id, work_title),
                            "title": item["title"],
                            "body_html": content,
                            "source_url": ep_url,
                        })

                    if progress_callback:
                        progress_callback(
                            i,
                            i / max(len(missing_episodes), 1) * 100,
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
                and str(item["id"]) in ep_content_map
            ):

                buffer.append({
                    "id": item["id"],
                    "title": item["title"],
                    "body": ep_content_map[str(item["id"])]
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

            for entry in extract_narou_toc_entries(soup):
                if entry["type"] == "chapter":
                    structure.append({
                        "type": "chapter",
                        "title": entry["title"],
                    })
                elif entry["id"] not in seen_ids:
                    seen_ids.add(entry["id"])
                    structure.append({
                        "type": "episode",
                        "id": entry["id"],
                        "title": entry["title"],
                    })

            next_page_url = find_narou_next_toc_url(soup, current_idx)
            if next_page_url:
                current_idx = next_page_url

                time.sleep(0.5)

            else:
                current_idx = None

                # 作品ページを取得できなかった場合
        if not work_title:
            raise Exception(
                "作品ページを取得できませんでした。"
                "小説家になろう側でアクセスが拒否されました（403 Forbidden）。"
            )

        ep_list = [
            s for s in structure
            if s["type"] == "episode"
        ]

        ep_content_map = build_episode_cache(cached_episodes)

        chapter_by_episode = {}
        current_chapter_title = work_title
        for structure_item in structure:
            if structure_item["type"] == "chapter":
                current_chapter_title = structure_item["title"]
            elif structure_item["type"] == "episode":
                chapter_by_episode[str(structure_item["id"])] = current_chapter_title

        filtered_ep_list = find_missing_episodes(
            ep_list,
            ep_content_map,
            start_episode=start_episode,
        )

        for i, item in enumerate(filtered_ep_list, 1):

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

                html = extract_narou_body(ep_soup)

                if not html:
                    if log_callback:
                        log_callback(f"本文を取得できませんでした: {item['title']}")
                    continue

                episode_id = str(item["id"])
                ep_content_map[episode_id] = html

                if chapter_callback:
                    chapter_callback({
                        "episode_id": episode_id,
                        "episode_index": ep_list.index(item) + 1,
                        "chapter_title": chapter_by_episode.get(episode_id, work_title),
                        "title": item["title"],
                        "body_html": html,
                        "source_url": ep_url,
                    })

                if progress_callback:
                    progress_callback(
                        i,
                        i / max(len(filtered_ep_list), 1) * 100,
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
                and str(item["id"]) in ep_content_map
            ):

                buffer.append({
                    "id": item["id"],
                    "title": item["title"],
                    "body": ep_content_map[str(item["id"])]
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
    current_soup = soup
    visited = set()

    while current_url:
        if current_url in visited:
            break
        visited.add(current_url)

        if not current_soup:
            break

        # 目次ページだけを巡回し、本文ページはまだ開かない。
        for entry in extract_narou_toc_entries(current_soup):
            if entry["type"] == "episode":
                episode_urls.add(urljoin(current_url, entry["href"]))

        next_page_url = find_narou_next_toc_url(current_soup, current_url)
        if next_page_url:
            current_url = next_page_url
            current_soup = get_soup(session, current_url)
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

    res = session.get(search_url)
    validate_response(res, search_url)

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
            headers={"Content-Type": "application/json"}
        )
        validate_response(res, "https://kakuyomu.jp/graphql")
        data = res.json()
    except (WorkNotFoundError, AccessRestrictedError, NovelNetworkError):
        raise
    except (requests.RequestException, ValueError) as exc:
        raise NovelNetworkError("カクヨムの話数情報を取得できませんでした。") from exc

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

        count = len(structure)
        if count == 0:
            raise NovelNetworkError(
                "小説家になろうの話数を解析できませんでした。時間を空けて再試行してください。"
            )
        return count

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
            raise NovelNetworkError("カクヨムの作品ページを取得できませんでした。")

        next_data_script = soup.find("script", id="__NEXT_DATA__")

        if not next_data_script:
            raise NovelNetworkError("カクヨムの作品情報を解析できませんでした。")

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
            raise NovelNetworkError("カクヨムの作品情報が見つかりませんでした。")

        work = apollo[work_key]

        # 目次構造取得
        toc_key = work.get("tableOfContents", {}).get("__ref")
        if not toc_key or toc_key not in apollo:
            raise NovelNetworkError("カクヨムの目次情報が見つかりませんでした。")

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
        if not episode_list:
            raise NovelNetworkError("カクヨムの話数を解析できませんでした。")
        return len(episode_list)

    else:
        raise ValueError("対応していないURLです。なろう、またはカクヨムの作品URLを入力してください。")

