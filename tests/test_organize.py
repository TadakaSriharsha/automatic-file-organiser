import unittest
import zipfile
import tempfile
from io import BytesIO
from pathlib import Path

import templates.app as app_module
from templates.app import DOWNLOAD_FOLDER, app, download_organized_files


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.metadata_directory = tempfile.TemporaryDirectory()
        app_module.METADATA_FILE = Path(self.metadata_directory.name) / "library_metadata.json"
        self.client = app.test_client()

    def tearDown(self):
        self.metadata_directory.cleanup()

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

    def test_text_preview_returns_real_file_content(self):
        response = self.organize([("phase1-preview.txt", b"FileFlow preview text")])
        self.assertEqual(response.status_code, 200)

        preview = self.client.get("/preview/Documents/phase1-preview.txt")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json["filename"], "phase1-preview.txt")
        self.assertEqual(preview.json["content"], "FileFlow preview text")

    def test_pdf_preview_and_library_download_are_available(self):
        response = self.organize([("phase1-preview.pdf", b"%PDF-1.4 test")])
        self.assertEqual(response.status_code, 200)

        preview = self.client.get("/preview/Documents/phase1-preview.pdf")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "application/pdf")
        self.assertIn("inline", preview.headers["Content-Disposition"])

        download = self.client.get("/files/download/Documents/phase1-preview.pdf")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"%PDF-1.4 test")

    def test_bulk_favorite_tag_and_collection_actions_persist(self):
        response = self.organize([("phase1-bulk.txt", b"bulk metadata")])
        self.assertEqual(response.status_code, 200)

        favorite = self.client.post("/api/files/bulk", json={
            "paths": ["Documents/phase1-bulk.txt"],
            "action": "favorite"
        })
        self.assertEqual(favorite.status_code, 200)

        tagged = self.client.post("/api/files/bulk", json={
            "paths": ["Documents/phase1-bulk.txt"],
            "action": "add_tag",
            "value": "important"
        })
        self.assertEqual(tagged.status_code, 200)

        collected = self.client.post("/api/files/bulk", json={
            "paths": ["Documents/phase1-bulk.txt"],
            "action": "collection",
            "value": "Work"
        })
        self.assertEqual(collected.status_code, 200)

        files = self.client.get("/api/files")
        file = next(item for item in files.json["files"] if item["relative_path"] == "Documents/phase1-bulk.txt")
        self.assertTrue(file["favorite"])
        self.assertIn("important", file["tags"])
        self.assertEqual(file["collection"], "Work")
        self.assertIn("Work", files.json["collections"])

    def test_ai_routes_are_not_available(self):
        self.assertEqual(self.client.post("/ai-edit").status_code, 404)
        self.assertEqual(self.client.post("/ai-review").status_code, 404)

    def test_rejects_empty_upload(self):
        response = self.client.post("/organize", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("select files", response.json["error"].lower())


if __name__ == "__main__":
    unittest.main()
