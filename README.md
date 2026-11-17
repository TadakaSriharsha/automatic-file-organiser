# Fillo — File Organisor

Fillo sorts uploaded files (or files selected from a sample folder in browsers with folder-picker support) into Images, Documents, Videos, Music, Code, Archives, and Others. It returns an organized ZIP download and never moves or changes the originals. The dashboard shows run statistics and recent organization history, including filenames and categories, for the current browser session. Fillo also reviews PDF/Word/text documents for writing suggestions and can edit Word document paragraphs with Gemini AI.

## Run locally

```powershell
python -m pip install -r requirements.txt
python app.py
```

Gemini review and editing require a server-side `GEMINI_API_KEY`. Document review supports text-based PDF, DOCX, and UTF-8 TXT files; scanned PDFs need OCR first. Review suggestions focus on writing and do not fact-check claims or search the web. Both AI features send document text to Gemini only after the user gives consent. Never put the key in browser code or commit it to the repository.

## Deploy on Render

1. Sign in to [Render](https://dashboard.render.com/) and choose **New → Blueprint**.
2. Connect `tadakasriharsha5-maker/automatic-file-organiser` and select the `main` branch.
3. Apply the Blueprint. Render reads `render.yaml` and creates the public web service.
4. To enable Gemini editing, add a newly rotated `GEMINI_API_KEY` under the service’s **Environment** settings, then redeploy.

The free service uses temporary storage. Organized ZIP downloads are private, one-time links that expire after 30 minutes. AI reviews and edits share a limit of 10 requests per client IP per hour.
