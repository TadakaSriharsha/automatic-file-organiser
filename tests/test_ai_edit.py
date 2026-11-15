import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch

from docx import Document

from templates.app import app


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


if __name__ == "__main__":
    unittest.main()
