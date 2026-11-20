import hashlib
import os
import re
import secrets
import tempfile
import time
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FOLDER = BASE_DIR
STATIC_FOLDER = BASE_DIR / "statics"

app = Flask(__name__, template_folder=str(TEMPLATE_FOLDER), static_folder=str(STATIC_FOLDER), static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

MAX_UPLOAD_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 100
DOWNLOAD_TTL_SECONDS = 30 * 60
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE + 1024 * 1024

DOWNLOAD_FOLDER = Path(tempfile.gettempdir()) / "fillo-downloads"
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

FILE_TYPES = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".heic"],
    "Documents": [".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".csv", ".ppt", ".pptx", ".xls", ".xlsx"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".webm"],
    "Music": [".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"],
    "Code": [".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".rb", ".sh", ".sql", ".xml", ".yaml", ".yml", ".toml"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "Others": []
}


def get_category(filename):
    extension = Path(filename).suffix.lower()
    for category, extensions in FILE_TYPES.items():
        if extension in extensions:
            return category
    return "Others"


def unique_path(folder, filename):
    destination = folder / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while destination.exists():
        destination = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination


def cleanup_expired_downloads():
    expiration_time = time.time() - DOWNLOAD_TTL_SECONDS
    for archive in DOWNLOAD_FOLDER.glob("*.zip"):
        try:
            if archive.stat().st_mtime < expiration_time:
                archive.unlink()
        except FileNotFoundError:
            pass


def remove_download(archive):
    try:
        archive.unlink()
    except FileNotFoundError:
        pass


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/organize", methods=["POST"])
def organize_files():
    files = request.files.getlist("files")
    if not files or all(not file.filename for file in files):
        return jsonify({"error": "Please select files first."}), 400

    files = [file for file in files if file.filename]
    if len(files) > MAX_FILES_PER_UPLOAD:
        return jsonify({"error": f"Choose no more than {MAX_FILES_PER_UPLOAD} files at a time."}), 413

    total_size = 0
    for file in files:
        file.stream.seek(0, os.SEEK_END)
        total_size += file.stream.tell()
        file.stream.seek(0)
        if total_size > MAX_UPLOAD_SIZE:
            return jsonify({"error": "Total upload size exceeds 20 MB."}), 413

    skip_duplicates = request.form.get("skip_duplicates") == "true"
    cleanup_expired_downloads()
    results = []
    seen_content = set()
    token = secrets.token_urlsafe(32)
    download_path = DOWNLOAD_FOLDER / f"{token}.zip"

    with tempfile.TemporaryDirectory(prefix="fillo-organize-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        organized_root = temporary_root / "organized"

        for file in files:
            filename = secure_filename(file.filename)
            if not filename:
                continue

            content = file.read()
            digest = hashlib.sha256(content).digest() if skip_duplicates else None
            duplicate = digest in seen_content if digest is not None else False
            if digest is not None:
                seen_content.add(digest)

            category = get_category(filename)
            destination = unique_path(organized_root / category, filename)
            if not (skip_duplicates and duplicate):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            results.append({
                "filename": destination.name,
                "category": category,
                "size": len(content),
                "duplicate": duplicate
            })

        if not results:
            return jsonify({"error": "No valid files were uploaded."}), 400

        temporary_archive = temporary_root / "organized-files.zip"
        with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in organized_root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(organized_root).as_posix())
        os.replace(temporary_archive, download_path)

    duplicate_count = sum(file["duplicate"] for file in results)
    included_count = len(results) - duplicate_count if skip_duplicates else len(results)
    return jsonify({
        "message": "Files organized successfully!",
        "total": len(results),
        "included": included_count,
        "duplicates": duplicate_count,
        "skip_duplicates": skip_duplicates,
        "files": results,
        "download_url": url_for("download_organized_files", token=token)
    })


@app.route("/download/<token>", methods=["GET"])
def download_organized_files(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return jsonify({"error": "This download link is invalid or has expired."}), 404

    cleanup_expired_downloads()
    archive = DOWNLOAD_FOLDER / f"{token}.zip"
    if not archive.is_file():
        return jsonify({"error": "This download link is invalid or has expired."}), 404

    response = send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name="fillo-organized-files.zip"
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.call_on_close(lambda: remove_download(archive))
    return response


@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"error": "Total upload size exceeds 20 MB."}), 413


if __name__ == "__main__":
    app.run(debug=True)
