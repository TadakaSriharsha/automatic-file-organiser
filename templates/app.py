
import os
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FOLDER = BASE_DIR
STATIC_FOLDER = BASE_DIR / "statics"

app = Flask(__name__, template_folder=str(TEMPLATE_FOLDER), static_folder=str(STATIC_FOLDER), static_url_path="/static")

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

UPLOAD_FOLDER = BASE_DIR / "uploads"
ORGANIZED_FOLDER = BASE_DIR / "Organized_Files"

UPLOAD_FOLDER.mkdir(exist_ok=True)
ORGANIZED_FOLDER.mkdir(exist_ok=True)

FILE_TYPES = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".pptx", ".xlsx"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi"],
    "Audio": [".mp3", ".wav", ".aac", ".ogg"],
    "Archives": [".zip", ".rar", ".7z"],
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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/organize", methods=["POST"])
def organize_files():
    files = request.files.getlist("files")

    if not files or all(not file.filename for file in files):
        return jsonify({"error": "Please select files first."}), 400

    results = []

    for file in files:
        if not file.filename:
            continue

        filename = secure_filename(file.filename)

        if not filename:
            continue

        category = get_category(filename)

        category_folder = ORGANIZED_FOLDER / category
        category_folder.mkdir(exist_ok=True)

        destination = unique_path(category_folder, filename)

        file.save(destination)

        results.append({
            "filename": filename,
            "category": category
        })

    if not results:
        return jsonify({"error": "No valid files were uploaded."}), 400

    return jsonify({
        "message": "Files organized successfully!",
        "total": len(results),
        "files": results
    })


@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"error": "Total upload size exceeds 20 MB."}), 413


if __name__ == "__main__":
    app.run(debug=True)
