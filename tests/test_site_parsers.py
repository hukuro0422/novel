import unittest

from bs4 import BeautifulSoup

from site_parsers import (
    extract_kakuyomu_body,
    extract_narou_body,
    extract_narou_toc_entries,
    find_narou_next_toc_url,
)


class SiteParserTests(unittest.TestCase):
    def test_kakuyomu_body_escapes_text_and_flattens_ruby(self):
        soup = BeautifulSoup(
            """
            <div class="widget-episodeBody">
              <p>猫 &amp; 犬</p>
              <p><ruby>春希<rt>はるき</rt></ruby></p>
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(
            extract_kakuyomu_body(soup),
            "<p>猫 &amp; 犬</p><p>春希</p>",
        )

    def test_narou_excludes_preface_and_afterword(self):
        soup = BeautifulSoup(
            """
            <div class="js-novel-text p-novel__text--preface"><p>前書き</p></div>
            <div class="js-novel-text"><p>本文</p><p>次の段落</p></div>
            <div class="js-novel-text p-novel__text--afterword"><p>後書き</p></div>
            """,
            "html.parser",
        )
        self.assertEqual(
            extract_narou_body(soup),
            "<p>本文</p><p>次の段落</p>",
        )

    def test_missing_body_returns_empty_string(self):
        soup = BeautifulSoup("<main>empty</main>", "html.parser")
        self.assertEqual(extract_kakuyomu_body(soup), "")
        self.assertEqual(extract_narou_body(soup), "")

    def test_narou_toc_extracts_chapters_and_episodes_in_order(self):
        soup = BeautifulSoup(
            """
            <div class="p-eplist">
              <div class="p-eplist__chapter-title">第一章</div>
              <div class="p-eplist__sublist"><a class="p-eplist__subtitle" href="/n1234ab/1/">一話</a></div>
              <div class="p-eplist__sublist"><a class="p-eplist__subtitle" href="/n1234ab/2/">二話</a></div>
            </div>
            """,
            "html.parser",
        )
        entries = extract_narou_toc_entries(soup)
        self.assertEqual([entry["type"] for entry in entries], ["chapter", "episode", "episode"])
        self.assertEqual([entry["id"] for entry in entries[1:]], ["1", "2"])

    def test_narou_toc_follows_next_page(self):
        soup = BeautifulSoup(
            '<nav><a rel="next" href="?p=2">次へ</a></nav>',
            "html.parser",
        )
        self.assertEqual(
            find_narou_next_toc_url(soup, "https://ncode.syosetu.com/n1234ab/"),
            "https://ncode.syosetu.com/n1234ab/?p=2",
        )
