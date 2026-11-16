import json
import os
import unittest
from io import BytesIO
import zipfile
from unittest.mock import patch

from docx import Document
from pypdf import PdfWriter

from templates.app import (
    AI_EDIT_LIMIT,
    DOWNLOAD_FOLDER,
    _ai_edits_by_ip,
    app,
    consume_ai_edit_allowance,
    download_organized_files
)


class FakeGeminiResponse:
    def __init__(self, paragraphs):
        edited = json.dumps({"paragraphs": paragraphs})
        self.payload = json.dumps({
            "candidates": [{"content": {"parts": [{"text": edited}]}}]
        }).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class FakeGeminiReviewResponse:
    def __init__(self, review):
        response_text = json.dumps(review)
        self.payload = json.dumps({
            "candidates": [{"content": {"parts": [{"text": response_text}]}}]
        }).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class AIEditRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        document = Document()
        document.add_heading("Project update", level=1)
        document.add_paragraph("The current draft needs a review.")
        document.add_table(rows=1, cols=1).cell(0, 0).text = "Keep this table local."
        buffer = BytesIO()
        document.save(buffer)
        self.document_bytes = buffer.getvalue()

    def post_document(self, consent="true"):
        return self.client.post(
            "/ai-edit",
            data={
                "document": (BytesIO(self.document_bytes), "draft.docx"),
                "instruction": "Make the second paragraph clearer.",
                "consent": consent
            },
            content_type="multipart/form-data"
        )

    def test_requires_server_api_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with patch("templates.app.urlopen") as mock_urlopen:
                response = self.client.post("/ai-edit")

        self.assertEqual(response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", response.json["error"])
        mock_urlopen.assert_not_called()

    def test_requires_consent_before_calling_gemini(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen") as mock_urlopen:
                response = self.post_document(consent="false")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm", response.json["error"])
        mock_urlopen.assert_not_called()

    def test_ai_edits_are_rate_limited_per_client(self):
        client_ip = "198.51.100.42"
        with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": client_ip}):
            for _ in range(AI_EDIT_LIMIT):
                self.assertEqual(consume_ai_edit_allowance(), 0)
            self.assertGreater(consume_ai_edit_allowance(), 0)
        _ai_edits_by_ip.pop(client_ip, None)

    def test_returns_edited_copy_and_sends_only_paragraph_text(self):
        model_response = FakeGeminiResponse([
            {"id": 0, "text": "Project update"},
            {"id": 1, "text": "The current draft is ready for review."}
        ])
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen", return_value=model_response) as mock_urlopen:
                response = self.post_document()

        self.assertEqual(response.status_code, 200)
        self.assertIn("draft_edited.docx", response.headers["Content-Disposition"])
        edited = Document(BytesIO(response.data))
        original = Document(BytesIO(self.document_bytes))
        self.assertEqual(edited.paragraphs[1].text, "The current draft is ready for review.")
        self.assertEqual(original.paragraphs[1].text, "The current draft needs a review.")
        self.assertEqual(edited.tables[0].cell(0, 0).text, "Keep this table local.")

        api_request = mock_urlopen.call_args.args[0]
        self.assertEqual(api_request.get_header("X-goog-api-key"), "test-key-not-real")
        self.assertIn(b"The current draft needs a review.", api_request.data)
        self.assertNotIn(b"Keep this table local.", api_request.data)

    def test_ai_review_requires_consent_before_calling_gemini(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen") as mock_urlopen:
                response = self.client.post(
                    "/ai-review",
                    data={
                        "document": (BytesIO(b"Some document text."), "draft.txt"),
                        "consent": "false"
                    },
                    content_type="multipart/form-data"
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm", response.json["error"])
        mock_urlopen.assert_not_called()

    def test_ai_review_returns_suggestions_for_text_document(self):
        model_response = FakeGeminiReviewResponse({
            "summary": "A clear draft with room to add context.",
            "strengths": ["The main point is easy to find."],
            "suggestions": [
                {"title": "Add context", "detail": "Explain who the update is for."}
            ]
        })
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen", return_value=model_response) as mock_urlopen:
                response = self.client.post(
                    "/ai-review",
                    data={
                        "document": (BytesIO(b"Project update: ready for review."), "draft.txt"),
                        "consent": "true"
                    },
                    content_type="multipart/form-data"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["suggestions"][0]["title"], "Add context")
        self.assertEqual(response.json["strengths"], ["The main point is easy to find."])
        api_request = mock_urlopen.call_args.args[0]
        self.assertEqual(api_request.get_header("X-goog-api-key"), "test-key-not-real")
        self.assertIn(b"Project update: ready for review.", api_request.data)

    def test_ai_review_includes_word_document_tables(self):
        model_response = FakeGeminiReviewResponse({
            "summary": "The draft is straightforward.",
            "strengths": [],
            "suggestions": [
                {"title": "Clarify the table", "detail": "Add context for the table's values."}
            ]
        })
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen", return_value=model_response) as mock_urlopen:
                response = self.client.post(
                    "/ai-review",
                    data={
                        "document": (BytesIO(self.document_bytes), "draft.docx"),
                        "consent": "true"
                    },
                    content_type="multipart/form-data"
                )

        self.assertEqual(response.status_code, 200)
        api_request = mock_urlopen.call_args.args[0]
        self.assertIn(b"Keep this table local.", api_request.data)

    def test_ai_review_reports_pdf_without_extractable_text(self):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        pdf = BytesIO()
        writer.write(pdf)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with patch("templates.app.urlopen") as mock_urlopen:
                response = self.client.post(
                    "/ai-review",
                    data={
                        "document": (BytesIO(pdf.getvalue()), "scan.pdf"),
                        "consent": "true"
                    },
                    content_type="multipart/form-data"
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("No readable text", response.json["error"])
        mock_urlopen.assert_not_called()

    def test_organize_returns_temporary_downloadable_zip(self):
        response = self.client.post(
            "/organize",
            data={"files": [
                (BytesIO(b"image bytes"), "photo.png"),
                (BytesIO(b"meeting notes"), "notes.txt")
            ]},
            content_type="multipart/form-data"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json
        self.assertEqual(payload["total"], 2)
        self.assertTrue(payload["download_url"].startswith("/download/"))

        token = payload["download_url"].rsplit("/", 1)[-1]
        archive_path = DOWNLOAD_FOLDER / f"{token}.zip"
        self.assertTrue(archive_path.is_file())

        download = self.client.get(payload["download_url"])
        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(BytesIO(download.data)) as archive:
            self.assertEqual(set(archive.namelist()), {"Images/photo.png", "Documents/notes.txt"})
            self.assertEqual(archive.read("Documents/notes.txt"), b"meeting notes")
        download.close()

        with app.test_request_context(payload["download_url"]):
            server_response = download_organized_files(token)
            self.assertEqual(len(server_response._on_close), 1)
            server_response.close()
        self.assertFalse(archive_path.exists())


if __name__ == "__main__":
    unittest.main()
