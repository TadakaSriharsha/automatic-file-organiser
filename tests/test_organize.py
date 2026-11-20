import unittest
import zipfile
from io import BytesIO

from templates.app import DOWNLOAD_FOLDER, app, download_organized_files


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def organize(self, files, skip_duplicates=False):
        data = {"files": [(BytesIO(content), filename) for filename, content in files]}
        if skip_duplicates:
            data["skip_duplicates"] = "true"
        return self.client.post("/organize", data=data, content_type="multipart/form-data")

    def zip_contents(self, response):
        token = response.json["download_url"].rsplit("/", 1)[-1]
        archive_path = DOWNLOAD_FOLDER / f"{token}.zip"
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(BytesIO(archive_path.read_bytes())) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        with app.test_request_context(response.json["download_url"]):
            download = download_organized_files(token)
            self.assertEqual(download.status_code, 200)
            download.close()
        self.assertFalse(archive_path.exists())
        return contents

    def test_organize_returns_downloadable_zip_with_sizes_and_extended_types(self):
        response = self.organize([
            ("photo.svg", b"vector"),
            ("report.odt", b"document"),
            ("video.webm", b"video"),
            ("backup.tar", b"archive")
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total"], 4)
        self.assertEqual(response.json["included"], 4)
        self.assertEqual(response.json["duplicates"], 0)
        self.assertEqual(response.json["files"][0]["size"], 6)
        self.assertEqual(self.zip_contents(response), {
            "Images/photo.svg": b"vector",
            "Documents/report.odt": b"document",
            "Videos/video.webm": b"video",
            "Archives/backup.tar": b"archive"
        })

    def test_exact_duplicates_are_reported_and_optionally_excluded(self):
        files = [
            ("notes.txt", b"same bytes"),
            ("copy.txt", b"same bytes"),
            ("different.txt", b"other bytes")
        ]
        response = self.organize(files, skip_duplicates=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total"], 3)
        self.assertEqual(response.json["included"], 2)
        self.assertEqual(response.json["duplicates"], 1)
        self.assertTrue(response.json["files"][1]["duplicate"])
        self.assertEqual(self.zip_contents(response), {
            "Documents/notes.txt": b"same bytes",
            "Documents/different.txt": b"other bytes"
        })

    def test_duplicate_files_remain_in_zip_when_skip_is_disabled(self):
        response = self.organize([
            ("notes.txt", b"same bytes"),
            ("copy.txt", b"same bytes")
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["duplicates"], 1)
        self.assertEqual(self.zip_contents(response), {
            "Documents/notes.txt": b"same bytes",
            "Documents/copy.txt": b"same bytes"
        })

    def test_ai_routes_are_not_available(self):
        self.assertEqual(self.client.post("/ai-edit").status_code, 404)
        self.assertEqual(self.client.post("/ai-review").status_code, 404)

    def test_rejects_empty_upload(self):
        response = self.client.post("/organize", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("select files", response.json["error"].lower())


if __name__ == "__main__":
    unittest.main()
