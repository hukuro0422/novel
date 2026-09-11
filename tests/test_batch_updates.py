import unittest
from unittest.mock import patch, MagicMock

from novel_downloader import (
    extract_kakuyomu_work_id_from_url,
    extract_narou_ncode_from_url,
    fetch_latest_chapter_counts_batch,
)


class BatchUpdateTests(unittest.TestCase):
    def test_extract_kakuyomu_work_id(self):
        url = "https://kakuyomu.jp/works/16817330657705436671"
        self.assertEqual(extract_kakuyomu_work_id_from_url(url), "16817330657705436671")
        url_with_slash = "https://kakuyomu.jp/works/16817330657705436671/"
        self.assertEqual(extract_kakuyomu_work_id_from_url(url_with_slash), "16817330657705436671")

    def test_extract_narou_ncode(self):
        url = "https://ncode.syosetu.com/n2267be/"
        self.assertEqual(extract_narou_ncode_from_url(url), "n2267be")
        url_upper = "https://ncode.syosetu.com/N4830BU"
        self.assertEqual(extract_narou_ncode_from_url(url_upper), "n4830bu")

    @patch("novel_downloader.create_session")
    def test_fetch_latest_chapter_counts_batch(self, mock_create_session):
        mock_session = MagicMock()
        mock_create_session.return_value = mock_session

        # なろうAPIレスポンスのモック
        narou_res = MagicMock()
        narou_res.status_code = 200
        narou_res.json.return_value = [
            {"allcount": 1},
            {"title": "なろう作品", "ncode": "N2267BE", "general_all_no": 795},
        ]

        # カクヨムGraphQLレスポンスのモック
        kakuyomu_res = MagicMock()
        kakuyomu_res.status_code = 200
        kakuyomu_res.json.return_value = {
            "data": {
                "works": [
                    {"id": "16817330657705436671", "publicEpisodeCount": 1153}
                ]
            }
        }

        mock_session.get.return_value = narou_res
        mock_session.post.return_value = kakuyomu_res

        novels = [
            {"id": 1, "url": "https://ncode.syosetu.com/n2267be/", "title": "なろう"},
            {"id": 2, "url": "https://kakuyomu.jp/works/16817330657705436671", "title": "カクヨム"},
        ]

        results = fetch_latest_chapter_counts_batch(novels, session=mock_session)

        self.assertIn(1, results)
        self.assertEqual(results[1]["latest_chapter"], 795)
        self.assertIsNone(results[1]["error"])

        self.assertIn(2, results)
        self.assertEqual(results[2]["latest_chapter"], 1153)
        self.assertIsNone(results[2]["error"])

        # なろうAPI(get)とカクヨムAPI(post)がそれぞれ1回ずつ呼ばれたことを検証
        self.assertEqual(mock_session.get.call_count, 1)
        self.assertEqual(mock_session.post.call_count, 1)
