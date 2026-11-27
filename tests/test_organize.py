import unittest
import zipfile
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import templates.app as app_module
from templates.app import DOWNLOAD_FOLDER, app, download_organized_files


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        app_module.AUTH_RATE_LIMITS.clear()
        self.metadata_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.metadata_directory.name)
        app_module.DOWNLOAD_FOLDER = root / "downloads"
        app_module.UPLOAD_FOLDER = root / "uploads"
        app_module.ORGANIZED_FOLDER = root / "organized"
        app_module.HISTORY_FILE = root / "activity_history.json"
        app_module.METADATA_FILE = Path(self.metadata_directory.name) / "library_metadata.json"
        app_module.DATA_DIR = root / "data"
        app_module.DATABASE_FILE = app_module.DATA_DIR / "fileflow.sqlite3"
        app_module.DATA_DIR.mkdir(parents=True, exist_ok=True)
        app_module.init_database()
        app_module.DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
        app_module.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
        app_module.ORGANIZED_FOLDER.mkdir(parents=True, exist_ok=True)
        self.client = app.test_client()
        signup = self.client.post("/api/auth/signup", json={
            "full_name": "Test User",
            "email": "test@example.com",
            "password": "SecurePass123",
            "confirm_password": "SecurePass123",
            "terms": True
        })
        self.assertEqual(signup.status_code, 200)

    def tearDown(self):
        self.metadata_directory.cleanup()

    def organize(self, files, skip_duplicates=False):
        data = {"files": [(BytesIO(content), filename) for filename, content in files]}
        if skip_duplicates:
            data["skip_duplicates"] = "true"
        return self.client.post("/organize", data=data, content_type="multipart/form-data")

    def zip_contents(self, response):
        token = response.json["download_url"].rsplit("/", 1)[-1]
        archive_path = app_module.DOWNLOAD_FOLDER / f"{token}.zip"
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
        preview.close()

        download = self.client.get("/files/download/Documents/phase1-preview.pdf")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"%PDF-1.4 test")
        download.close()

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

    def test_custom_folders_move_rename_and_delete_files_without_overwriting(self):
        organized = self.organize([("folder-item.txt", b"folder data")])
        self.assertEqual(organized.status_code, 200)

        created = self.client.post("/api/folders", json={"name": "Study Notes"})
        self.assertEqual(created.status_code, 201)
        moved = self.client.post("/api/files/bulk", json={
            "paths": ["Documents/folder-item.txt"],
            "action": "move",
            "value": "Study Notes"
        })
        self.assertEqual(moved.status_code, 200)

        renamed_folder = self.client.patch("/api/folders/Study%20Notes", json={"name": "Semester 1"})
        self.assertEqual(renamed_folder.status_code, 200)
        renamed_file = self.client.post("/api/files/rename", json={
            "path": "Folders/Semester 1/folder-item.txt",
            "name": "lecture notes.txt"
        })
        self.assertEqual(renamed_file.status_code, 200)

        files = self.client.get("/api/files").json["files"]
        file = next(item for item in files if item["filename"] == "lecture notes.txt")
        self.assertEqual(file["relative_path"], "Folders/Semester 1/lecture notes.txt")
        self.assertEqual(file["folder"], "Folders/Semester 1")
        self.assertEqual(self.client.delete("/api/folders/Semester%201").status_code, 409)

        moved_to_trash = self.client.post("/api/files/bulk", json={
            "paths": [file["relative_path"]],
            "action": "trash"
        })
        self.assertEqual(moved_to_trash.status_code, 200)
        self.assertEqual(self.client.delete("/api/folders/Semester%201").status_code, 200)

    def test_trash_restore_uses_a_safe_name_when_original_path_is_occupied(self):
        self.assertEqual(self.organize([("restore.txt", b"first version")]).status_code, 200)
        removed = self.client.post("/api/files/bulk", json={
            "paths": ["Documents/restore.txt"],
            "action": "trash"
        })
        self.assertEqual(removed.status_code, 200)
        trashed = self.client.get("/api/trash").json["files"]
        self.assertEqual(len(trashed), 1)

        self.assertEqual(self.organize([("restore.txt", b"new version")]).status_code, 200)
        restored = self.client.post(f"/api/trash/{trashed[0]['id']}")
        self.assertEqual(restored.status_code, 200)

        files = self.client.get("/api/files").json["files"]
        self.assertEqual({item["relative_path"] for item in files}, {
            "Documents/restore.txt",
            "Documents/restore_1.txt"
        })
        current = self.client.get("/files/download/Documents/restore.txt")
        restored_copy = self.client.get("/files/download/Documents/restore_1.txt")
        self.assertEqual(current.data, b"new version")
        self.assertEqual(restored_copy.data, b"first version")

    def test_recently_opened_timestamp_is_persisted_per_file(self):
        self.assertEqual(self.organize([("recent.txt", b"read me")]).status_code, 200)
        preview = self.client.get("/preview/Documents/recent.txt?opened=1")
        self.assertEqual(preview.status_code, 200)
        files = self.client.get("/api/files").json["files"]
        file = next(item for item in files if item["filename"] == "recent.txt")
        self.assertTrue(file["last_opened"])

    def test_ai_routes_are_not_available(self):
        self.assertEqual(self.client.post("/ai-edit").status_code, 404)
        self.assertEqual(self.client.post("/ai-review").status_code, 404)

    def test_rejects_empty_upload(self):
        response = self.client.post("/organize", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("select files", response.json["error"].lower())

    def test_landing_is_public_and_dashboard_requires_login(self):
        anonymous = app.test_client()
        self.assertEqual(anonymous.get("/").status_code, 200)
        protected = anonymous.get("/dashboard")
        self.assertEqual(protected.status_code, 302)
        self.assertIn("/signin", protected.location)

    def test_duplicate_registration_and_wrong_password_are_rejected(self):
        duplicate = self.client.post("/api/auth/signup", json={
            "full_name": "Duplicate",
            "email": "test@example.com",
            "password": "SecurePass123",
            "confirm_password": "SecurePass123",
            "terms": True
        })
        self.assertEqual(duplicate.status_code, 409)

        anonymous = app.test_client()
        wrong = anonymous.post("/api/auth/login", json={
            "email": "test@example.com",
            "password": "WrongPassword123"
        })
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn("registered", wrong.json["error"].lower())

    def test_logout_revokes_the_server_session(self):
        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 302)

    def test_forgot_password_uses_generic_response(self):
        unknown = app.test_client().post("/api/auth/forgot-password", json={
            "email": "unknown@example.com"
        })
        with patch.object(app_module, "send_reset_email", return_value=True) as sender:
            known = app.test_client().post("/api/auth/forgot-password", json={
                "email": "test@example.com"
            })
        expected = "If an account exists"
        self.assertIn(expected, unknown.json["message"])
        self.assertIn(expected, known.json["message"])
        sender.assert_called_once()

    def test_health_reports_unconfigured_email_delivery_without_secrets(self):
        with patch.dict("os.environ", {}, clear=True):
            response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["email_delivery"], "not_configured")

    def test_resend_delivery_requires_api_key_and_sender(self):
        with patch.dict("os.environ", {}, clear=True):
            sent = app_module.send_via_resend(
                "test@example.com",
                "Subject",
                "Text",
                "<p>Text</p>"
            )
        self.assertFalse(sent)

    def test_smtp_delivery_requires_host_and_sender(self):
        with patch.dict("os.environ", {}, clear=True):
            sent = app_module.send_via_smtp(
                "test@example.com",
                "Subject",
                "Text",
                "<p>Text</p>"
            )
        self.assertFalse(sent)

    def test_invalid_reset_token_is_rejected_and_valid_reset_changes_password(self):
        anonymous = app.test_client()
        with app_module.get_db() as connection:
            user = connection.execute("SELECT id FROM users WHERE email = ?", ("test@example.com",)).fetchone()
        token = app_module.reset_serializer().dumps({"uid": user["id"], "nonce": "test-reset"})
        token_hash = __import__("hashlib").sha256(token.encode("utf-8")).hexdigest()
        with app_module.get_db() as connection:
            connection.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (user["id"], token_hash, __import__("time").time() + 1800, "2026-11-22")
            )

        invalid = anonymous.post("/api/auth/reset-password", json={
            "token": "invalid-token",
            "password": "NewSecurePass123",
            "confirm_password": "NewSecurePass123"
        })
        self.assertEqual(invalid.status_code, 400)

        reset = anonymous.post("/api/auth/reset-password", json={
            "token": token,
            "password": "NewSecurePass123",
            "confirm_password": "NewSecurePass123"
        })
        self.assertEqual(reset.status_code, 200)

    def test_users_cannot_access_another_users_file(self):
        owned = self.organize([("private.txt", b"user one data")])
        self.assertEqual(owned.status_code, 200)

        second = app.test_client()
        signup = second.post("/api/auth/signup", json={
            "full_name": "Second User",
            "email": "second@example.com",
            "password": "SecurePass456",
            "confirm_password": "SecurePass456",
            "terms": True
        })
        self.assertEqual(signup.status_code, 200)

        preview = second.get("/preview/Documents/private.txt")
        download = second.get("/files/download/Documents/private.txt")
        self.assertEqual(preview.status_code, 404)
        self.assertEqual(download.status_code, 404)


if __name__ == "__main__":
    unittest.main()
