import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, url_for
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
UPLOAD_FOLDER = BASE_DIR.parent / "uploads"
ORGANIZED_FOLDER = BASE_DIR / "Organized_Files"
HISTORY_FILE = BASE_DIR / "activity_history.json"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ORGANIZED_FOLDER.mkdir(parents=True, exist_ok=True)

FILE_TYPES = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".heic"],
    "Documents": [".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md"],
    "Spreadsheets": [".csv", ".xls", ".xlsx", ".ods"],
    "Presentations": [".ppt", ".pptx", ".odp", ".key"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".webm"],
    "Audio": [".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"],
    "Code": [".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".rb", ".sh", ".sql", ".xml", ".yaml", ".yml", ".toml"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "Others": []
}

RISKY_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".dll", ".msi", ".scr", ".vbs", ".vbe",
    ".jse", ".jar", ".ps1", ".psm1", ".lnk", ".hta"
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


def format_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def load_history():
    if not HISTORY_FILE.is_file():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(entries):
    try:
        HISTORY_FILE.write_text(json.dumps(entries[:100], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_within_directory(directory, target):
    try:
        target.resolve().relative_to(directory.resolve())
        return True
    except (ValueError, OSError):
        return False


def build_library():
    files = []
    if ORGANIZED_FOLDER.is_dir():
        for path in ORGANIZED_FOLDER.rglob("*"):
            if not path.is_file():
                continue
            if not is_within_directory(ORGANIZED_FOLDER, path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            category = path.relative_to(ORGANIZED_FOLDER).parts[0] if len(path.relative_to(ORGANIZED_FOLDER).parts) > 1 else get_category(path.name)
            files.append({
                "filename": path.name,
                "category": category,
                "size": stat.st_size,
                "size_label": format_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "relative_path": path.relative_to(ORGANIZED_FOLDER).as_posix(),
                "previewable": path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
            })
    return sorted(files, key=lambda item: item["modified"], reverse=True)


def build_stats():
    organized = build_library()
    uploaded = []
    if UPLOAD_FOLDER.is_dir():
        for path in UPLOAD_FOLDER.iterdir():
            if path.is_file():
                try:
                    uploaded.append({"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime})
                except OSError:
                    pass

    category_counts = {}
    category_sizes = {}
    extension_counts = {}
    for file in organized:
        category = file["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
        category_sizes[category] = category_sizes.get(category, 0) + file["size"]
        extension = Path(file["filename"]).suffix.lower() or "none"
        extension_counts[extension] = extension_counts.get(extension, 0) + 1

    return {
        "total_files": len(uploaded),
        "organized_files": len(organized),
        "storage_used": sum(file["size"] for file in organized),
        "storage_used_label": format_size(sum(file["size"] for file in organized)),
        "categories": len(category_counts),
        "category_counts": category_counts,
        "category_sizes": category_sizes,
        "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: item[1], reverse=True)[:8]),
        "recent_uploaded": sorted(uploaded, key=lambda item: item["modified"], reverse=True)[:8],
        "recent_organized": organized[:8],
        "history": load_history()[:20]
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    stats = build_stats()
    stats["recent_uploaded"] = [
        {
            "filename": item["name"],
            "size": item["size"],
            "size_label": format_size(item["size"]),
            "modified": datetime.fromtimestamp(item["modified"], timezone.utc).isoformat()
        }
        for item in stats["recent_uploaded"]
    ]
    return jsonify(stats)


@app.route("/api/files")
def api_files():
    return jsonify({"files": build_library()})


@app.route("/api/history")
def api_history():
    return jsonify({"history": load_history()})


@app.route("/preview/<path:relative_path>")
def preview_file(relative_path):
    target = (ORGANIZED_FOLDER / relative_path).resolve()
    if not is_within_directory(ORGANIZED_FOLDER, target) or not target.is_file():
        return jsonify({"error": "File not found."}), 404
    if target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
        return jsonify({"error": "Preview is not available for this file type."}), 415
    return send_from_directory(str(ORGANIZED_FOLDER), relative_path)


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
            if Path(filename).suffix.lower() in RISKY_EXTENSIONS:
                continue

            content = file.read()
            digest = hashlib.sha256(content).digest()
            duplicate = digest in seen_content
            seen_content.add(digest)

            category = get_category(filename)
            destination = unique_path(organized_root / category, filename)
            if not (skip_duplicates and duplicate):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                persistent_upload = unique_path(UPLOAD_FOLDER, filename)
                persistent_upload.write_bytes(content)
                persistent_destination = unique_path(ORGANIZED_FOLDER / category, filename)
                persistent_destination.parent.mkdir(parents=True, exist_ok=True)
                persistent_destination.write_bytes(content)
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
    included_results = [file for file in results if not (skip_duplicates and file["duplicate"])]
    categories_created = sorted({file["category"] for file in included_results})
    history = load_history()
    history.insert(0, {
        "id": secrets.token_hex(8),
        "date": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "files_processed": len(results),
        "files_organized": included_count,
        "duplicates": duplicate_count,
        "categories_created": categories_created,
        "filenames": [file["filename"] for file in included_results]
    })
    save_history(history)
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
