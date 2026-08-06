import unittest

from novel_downloader import (
    clean_filename,
    detect_site,
    normalize_kakuyomu_url,
    normalize_narou_url,
    get_latest_chapter_count,
)


class UrlTests(unittest.TestCase):
    def test_detect_site(self):
        cases = [
            ("https://ncode.syosetu.com/n1234ab/12/", "narou"),
            ("https://novel18.syosetu.com/n1234ab/", "narou"),
            ("https://kakuyomu.jp/works/123/episodes/456", "kakuyomu"),
            ("https://example.com/works/123", "unknown"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(detect_site(url), expected)

    def test_normalize_narou_episode_url(self):
        self.assertEqual(
            normalize_narou_url("https://ncode.syosetu.com/n1234ab/12/"),
            "https://ncode.syosetu.com/n1234ab/",
        )

    def test_normalize_kakuyomu_episode_url(self):
        self.assertEqual(
            normalize_kakuyomu_url("https://kakuyomu.jp/works/123/episodes/456"),
            "https://kakuyomu.jp/works/123",
        )

    def test_clean_filename_removes_windows_reserved_characters(self):
        self.assertEqual(clean_filename('a:b/c*?"d<e>|'), "abcde")

    def test_unsupported_site_is_not_treated_as_zero_chapters(self):
        with self.assertRaisesRegex(ValueError, "対応していないURL"):
            get_latest_chapter_count("https://example.com/works/123")
