# Fillo — File Organisor

Fillo organizes uploaded files into Images, Documents, Videos, Music, Code, Archives, and Others. It never moves or changes your originals and creates a downloadable ZIP. Exact duplicate files are identified by their contents; enable **Skip exact duplicate files** to leave duplicate copies out of the ZIP. Results can be searched, filtered by category, and sorted by name, category, or file size. Recent runs and file/category statistics are shown in the current browser session.

Fillo uses deterministic file-extension rules and SHA-256 checks for duplicate detection. It does not use AI, external AI APIs, or send document text to third-party services.

## Run locally

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open the local address printed by Flask. Uploads are limited to 20 MB total and 100 files per run. ZIP downloads are private, one-time links that expire after 30 minutes.

## Run tests

```powershell
python -m unittest discover -s tests
```

## Deploy on Render

1. Sign in to [Render](https://dashboard.render.com/) and choose **New → Blueprint**.
2. Connect `tadakasriharsha5-maker/automatic-file-organiser` and select the `main` branch.
3. Apply the Blueprint. Render reads `render.yaml` and creates the public web service.

The free service uses temporary storage. Files are only kept long enough to create the download; downloaded ZIPs are deleted after use.
