import unittest

from novel_downloader import build_episode_cache, find_missing_episodes


class ChapterCacheTests(unittest.TestCase):
    def test_build_cache_ignores_incomplete_rows(self):
        rows = [
            {"episode_id": "1", "body_html": "<p>one</p>"},
            {"episode_id": "2", "body_html": ""},
            {"episode_id": None, "body_html": "<p>bad</p>"},
        ]
        self.assertEqual(build_episode_cache(rows), {"1": "<p>one</p>"})

    def test_only_new_episodes_are_requested(self):
        episodes = [
            {"id": "1", "title": "one"},
            {"id": "2", "title": "two"},
            {"id": "3", "title": "three"},
        ]
        missing = find_missing_episodes(episodes, {"1": "cached", "2": "cached"})
        self.assertEqual([item["id"] for item in missing], ["3"])

    def test_cache_can_rebuild_full_book_when_there_is_no_update(self):
        episodes = [{"id": "1"}, {"id": "2"}]
        missing = find_missing_episodes(episodes, {"1": "a", "2": "b"})
        self.assertEqual(missing, [])

    def test_empty_cache_downloads_every_episode(self):
        episodes = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        missing = find_missing_episodes(episodes, {})
        self.assertEqual([episode["id"] for episode in missing], ["1", "2", "3"])

    def test_partial_cache_downloads_only_missing_episodes(self):
        episodes = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        missing = find_missing_episodes(episodes, {"1": "cached"})
        self.assertEqual([episode["id"] for episode in missing], ["2", "3"])
