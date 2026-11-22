# Fillo — File Organisor

Fillo is a premium smart file organizer with a responsive dashboard, file explorer, category preview, activity history, analytics, and dark/light themes. It organizes uploaded copies into Images, Documents, Spreadsheets, Presentations, Videos, Audio, Code, Archives, and Other folders. Originals are never moved or overwritten, duplicate filenames receive safe numbered names, and exact duplicate content can be excluded.

Fillo uses deterministic file-extension rules and SHA-256 checks for duplicate detection. Dashboard, explorer, category, and analytics data are generated from the local upload and organized-file directories. Organization sessions are persisted to `templates/activity_history.json` and downloads remain private, expiring ZIP links.

## Run locally

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open the local address printed by Flask. Uploads are limited to 20 MB total and 100 files per run. ZIP downloads are private links that expire after 30 minutes.

## Authentication setup

FileFlow uses SQLite for local users and server-tracked sessions. Set `FILEFLOW_SECRET_KEY` to a persistent random value. Without it, restarts invalidate existing sessions.

Copy `.env.example` to `.env` and configure the SMTP variables to enable verification and password-reset email. The free Render plan uses ephemeral `/tmp` storage, so users and files may be lost whenever Render restarts or redeploys the service. Persistent users and files require a paid Render disk plan.

Required production settings:

- `FILEFLOW_SECRET_KEY`
- `FILEFLOW_COOKIE_SECURE=true`
- `FILEFLOW_CORS_ORIGINS`
- `FILEFLOW_SMTP_HOST`
- `FILEFLOW_SMTP_PORT`
- `FILEFLOW_SMTP_USERNAME`
- `FILEFLOW_SMTP_PASSWORD`
- `FILEFLOW_SMTP_FROM`

## Run tests

```powershell
python -m unittest discover -s tests
```

## Deploy on Render

1. Sign in to [Render](https://dashboard.render.com/) and choose **New → Blueprint**.
2. Connect `tadakasriharsha5-maker/automatic-file-organiser` and select the `main` branch.
3. Apply the Blueprint. Render reads `render.yaml` and creates the public web service.

The free service uses temporary storage. Files are only kept long enough to create the download; downloaded ZIPs are deleted after use.
