import tempfile
import unittest
import zipfile
from pathlib import Path

from epub_builder import safe_filename, sanitize_body_html, write_epub


class EpubBuilderTests(unittest.TestCase):
    def test_safe_filename_handles_reserved_windows_name(self):
        self.assertEqual(safe_filename("CON"), "_CON")

    def test_body_html_removes_script_and_attributes(self):
        cleaned = sanitize_body_html(
            '<p class="x">本文</p><script>alert(1)</script><strong id="a">強調</strong>'
        )
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("alert(1)", cleaned)
        self.assertNotIn("class=", cleaned)
        self.assertNotIn("id=", cleaned)
        self.assertIn("<p>本文</p>", cleaned)

    def test_generated_epub_contains_navigation_and_all_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = write_epub(
                main_title="題名 & test",
                volume_title="第一章",
                episodes=[
                    {"id": "1", "title": "一話 <開始>", "body": "<p>本文1</p>"},
                    {"id": "2", "title": "二話", "body": "<p>本文2</p>"},
                ],
                file_index=1,
                folder_path=directory,
                site_name="テスト",
            )

            self.assertTrue(Path(output).exists())
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn("EPUB/nav.xhtml", names)
                episode_files = [name for name in names if "/episodes/" in name]
                self.assertEqual(len(episode_files), 2)
                first_episode = archive.read(episode_files[0]).decode("utf-8")
                self.assertIn("一話 &lt;開始&gt;", first_episode)
