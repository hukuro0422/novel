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

    def test_generated_epub_embeds_illustrations(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            dummy_img_path = Path(directory) / "test_illust.png"
            img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
            img.save(dummy_img_path, format="PNG")

            output = write_epub(
                main_title="挿絵テスト作品",
                volume_title="第一章",
                episodes=[
                    {
                        "id": "1",
                        "title": "挿絵のある回",
                        "body": f'<p>テキスト前</p><p class="illustration"><img src="{dummy_img_path.as_posix()}" alt="テスト挿絵"/></p><p>テキスト後</p>',
                    }
                ],
                file_index=1,
                folder_path=directory,
                site_name="テスト",
            )

            self.assertTrue(Path(output).exists())
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                image_files = [n for n in names if "images/illust_" in n]
                self.assertEqual(len(image_files), 1)
                self.assertTrue(image_files[0].endswith(".jpg"))

                episode_files = [n for n in names if "/episodes/" in n]
                content = archive.read(episode_files[0]).decode("utf-8")
                self.assertIn('src="../images/illust_0001.jpg"', content)
                self.assertIn('class="illustration-page"', content)
                self.assertIn("テキスト前", content)
                self.assertIn("テキスト後", content)

                css_content = archive.read("EPUB/styles/book.css").decode("utf-8")
                self.assertIn("page-break-before: always", css_content)
                self.assertIn("writing-mode: horizontal-tb", css_content)

    def test_cover_image_always_saved_as_cover_jpg(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            dummy_cover_png = Path(directory) / "my_custom_cover.png"
            img = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
            img.save(dummy_cover_png, format="PNG")

            output = write_epub(
                main_title="表紙テスト作品",
                volume_title="第一章",
                episodes=[{"id": "1", "title": "一話", "body": "<p>本文</p>"}],
                file_index=1,
                folder_path=directory,
                site_name="テスト",
                cover_path=str(dummy_cover_png),
            )

            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn("EPUB/cover.jpg", names)
                self.assertNotIn("EPUB/cover.png", names)

