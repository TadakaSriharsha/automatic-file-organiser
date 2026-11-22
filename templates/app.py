import hashlib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from email.message import EmailMessage
from functools import wraps

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, url_for, session, redirect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FOLDER = BASE_DIR
STATIC_FOLDER = BASE_DIR / "statics"

app = Flask(__name__, template_folder=str(TEMPLATE_FOLDER), static_folder=str(STATIC_FOLDER), static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["SECRET_KEY"] = os.environ.get("FILEFLOW_SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FILEFLOW_COOKIE_SECURE", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60

MAX_UPLOAD_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 100
DOWNLOAD_TTL_SECONDS = 30 * 60
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE + 1024 * 1024

DOWNLOAD_FOLDER = Path(tempfile.gettempdir()) / "fillo-downloads"
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER = BASE_DIR.parent / "uploads"
ORGANIZED_FOLDER = BASE_DIR / "Organized_Files"
HISTORY_FILE = BASE_DIR / "activity_history.json"
METADATA_FILE = BASE_DIR / "library_metadata.json"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ORGANIZED_FOLDER.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(os.environ.get("FILEFLOW_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_FILE = Path(os.environ.get("FILEFLOW_DB_PATH", str(DATA_DIR / "fileflow.sqlite3")))
DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
RESET_TOKEN_TTL = 60 * 30
AUTH_RATE_LIMITS = {}

FILE_TYPES = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".heic"],
    "Documents": [".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md"],
    "Spreadsheets": [".csv", ".xls", ".xlsx", ".ods"],
    "Presentations": [".ppt", ".pptx", ".odp", ".key"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".webm"],
    "Audio": [".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"],
    "Code": [".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".rb", ".sh", ".sql", ".xml", ".yaml", ".yml", ".toml"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "Other": []
}

RISKY_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".dll", ".msi", ".scr", ".vbs", ".vbe",
    ".jse", ".jar", ".ps1", ".psm1", ".lnk", ".hta"
}


def get_db():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database():
    with get_db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_reset_tokens_hash ON password_reset_tokens(token_hash);
        """)


init_database()


def normalize_email(email):
    return email.strip().lower()[:254]


def valid_email(email):
    return re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", email) is not None


def password_errors(password):
    errors = []
    if len(password) < 10:
        errors.append("Use at least 10 characters.")
    if not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password):
        errors.append("Include upper and lower case letters.")
    if not re.search(r"\d", password):
        errors.append("Include at least one number.")
    return errors


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as connection:
        return connection.execute(
            "SELECT id, full_name, email, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()


def login_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        if not current_user():
            session.clear()
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"error": "Please sign in to continue.", "login_required": True}), 401
            return redirect(url_for("signin", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return protected


def auth_rate_limited(key, limit=10, window_seconds=300):
    now = time.time()
    attempts = [timestamp for timestamp in AUTH_RATE_LIMITS.get(key, []) if timestamp > now - window_seconds]
    if len(attempts) >= limit:
        return True
    attempts.append(now)
    AUTH_RATE_LIMITS[key] = attempts
    return False


def reset_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="fileflow-password-reset")


def user_workspace(user_id):
    workspace = DATA_DIR / "users" / str(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "uploads").mkdir(parents=True, exist_ok=True)
    (workspace / "organized").mkdir(parents=True, exist_ok=True)
    return workspace


def current_workspace():
    user = current_user()
    if not user:
        return None
    return user_workspace(user["id"])


def get_category(filename):
    extension = Path(filename).suffix.lower()
    for category, extensions in FILE_TYPES.items():
        if extension in extensions:
            return category
    return "Other"


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


def load_history(workspace=None):
    history_file = (workspace / "activity_history.json") if workspace else HISTORY_FILE
    if not history_file.is_file():
        return []
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(entries, workspace=None):
    history_file = (workspace / "activity_history.json") if workspace else HISTORY_FILE
    try:
        history_file.write_text(json.dumps(entries[:100], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_metadata(workspace=None):
    metadata_file = (workspace / "library_metadata.json") if workspace else METADATA_FILE
    if not metadata_file.is_file():
        return {"files": {}, "collections": {}}
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"files": {}, "collections": {}}
        return {
            "files": data.get("files", {}) if isinstance(data.get("files"), dict) else {},
            "collections": data.get("collections", {}) if isinstance(data.get("collections"), dict) else {}
        }
    except (json.JSONDecodeError, OSError):
        return {"files": {}, "collections": {}}


def save_metadata(metadata, workspace=None):
    metadata_file = (workspace / "library_metadata.json") if workspace else METADATA_FILE
    try:
        metadata_file.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return True
    except OSError:
        return False


def clean_tags(values):
    cleaned = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        tag = re.sub(r"\s+", " ", value.strip())[:40]
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned[:20]


def is_within_directory(directory, target):
    try:
        target.resolve().relative_to(directory.resolve())
        return True
    except (ValueError, OSError):
        return False


def build_library():
    files = []
    workspace = current_workspace()
    organized_folder = workspace / "organized" if workspace else ORGANIZED_FOLDER
    metadata = load_metadata(workspace)
    file_metadata = metadata["files"]
    if organized_folder.is_dir():
        for path in organized_folder.rglob("*"):
            if not path.is_file():
                continue
            if not is_within_directory(organized_folder, path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            category = path.relative_to(organized_folder).parts[0] if len(path.relative_to(organized_folder).parts) > 1 else get_category(path.name)
            relative_path = path.relative_to(organized_folder).as_posix()
            item_metadata = file_metadata.get(relative_path, {})
            files.append({
                "filename": path.name,
                "category": category,
                "size": stat.st_size,
                "size_label": format_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "relative_path": relative_path,
                "previewable": path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".pdf", ".txt", ".md", ".csv", ".json", ".xml", ".html", ".css", ".js", ".log"},
                "preview_type": "image" if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"} else ("pdf" if path.suffix.lower() == ".pdf" else ("text" if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".css", ".js", ".log"} else "none")),
                "favorite": item_metadata.get("favorite") is True,
                "tags": item_metadata.get("tags", []) if isinstance(item_metadata.get("tags"), list) else [],
                "collection": item_metadata.get("collection") if isinstance(item_metadata.get("collection"), str) else ""
            })
    return sorted(files, key=lambda item: item["modified"], reverse=True)


def build_stats():
    organized = build_library()
    workspace = current_workspace()
    upload_folder = workspace / "uploads" if workspace else UPLOAD_FOLDER
    uploaded = []
    if upload_folder.is_dir():
        for path in upload_folder.iterdir():
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
        "history": load_history(workspace)[:20]
    }


@app.route("/")
def home():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    return render_template("index.html", user={
        "id": user["id"],
        "name": user["full_name"],
        "email": user["email"],
        "created_at": user["created_at"]
    })


@app.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("dashboard"))
        return render_template("signin.html")

    payload = request.get_json(silent=True) or request.form
    email = normalize_email(str(payload.get("email", "")))
    password = str(payload.get("password", ""))
    remember = str(payload.get("remember", "")).lower() in {"true", "on", "1", "yes"}
    if not email or not password:
        return jsonify({"error": "Enter your email and password."}), 400
    if auth_rate_limited(f"signin:{request.remote_addr}", limit=10):
        return jsonify({"error": "Too many sign-in attempts. Please try again later."}), 429

    with get_db() as connection:
        user = connection.execute("SELECT id, full_name, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Email or password is incorrect."}), 401

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = remember
    return jsonify({"redirect": url_for("dashboard"), "name": user["full_name"]})


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("dashboard"))
        return render_template("signup.html")

    payload = request.get_json(silent=True) or request.form
    full_name = re.sub(r"\s+", " ", str(payload.get("full_name", "")).strip())[:100]
    email = normalize_email(str(payload.get("email", "")))
    password = str(payload.get("password", ""))
    confirm_password = str(payload.get("confirm_password", ""))
    terms = str(payload.get("terms", "")).lower() in {"true", "on", "1", "yes"}

    if not full_name or not email or not password or not confirm_password:
        return jsonify({"error": "Complete every field."}), 400
    if not valid_email(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if password_errors(password):
        return jsonify({"error": " ".join(password_errors(password))}), 400
    if password != confirm_password:
        return jsonify({"error": "Password and confirmation do not match."}), 400
    if not terms:
        return jsonify({"error": "Accept the Terms and Privacy Policy to continue."}), 400
    if auth_rate_limited(f"signup:{request.remote_addr}", limit=8):
        return jsonify({"error": "Too many registration attempts. Please try again later."}), 429

    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as connection:
            cursor = connection.execute(
                "INSERT INTO users (full_name, email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), now, now)
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with this email already exists."}), 409

    user_workspace(user_id)
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    return jsonify({"redirect": url_for("dashboard"), "name": full_name})


def send_reset_email(email, reset_url):
    host = os.environ.get("FILEFLOW_SMTP_HOST")
    port = int(os.environ.get("FILEFLOW_SMTP_PORT", "587"))
    username = os.environ.get("FILEFLOW_SMTP_USERNAME")
    password = os.environ.get("FILEFLOW_SMTP_PASSWORD")
    sender = os.environ.get("FILEFLOW_SMTP_FROM", username)
    use_tls = os.environ.get("FILEFLOW_SMTP_TLS", "true").lower() == "true"
    if not host or not sender:
        app.logger.warning("Password reset email requested but SMTP is not configured.")
        return False

    message = EmailMessage()
    message["Subject"] = "Reset your FileFlow password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "We received a request to reset your FileFlow password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 30 minutes. If you did not request it, you can ignore this email."
    )
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError, TimeoutError) as error:
        app.logger.error("Password reset email failed: %s", error)
        return False


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    payload = request.get_json(silent=True) or request.form
    email = normalize_email(str(payload.get("email", "")))
    if auth_rate_limited(f"forgot:{request.remote_addr}", limit=6):
        return jsonify({
            "message": "If an account exists with this email, you'll receive a password reset link shortly."
        })

    if email and valid_email(email):
        with get_db() as connection:
            user = connection.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            nonce = secrets.token_urlsafe(32)
            token = reset_serializer().dumps({"uid": user["id"], "nonce": nonce})
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            expires_at = int(time.time()) + RESET_TOKEN_TTL
            with get_db() as connection:
                connection.execute(
                    "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (user["id"], token_hash, expires_at, datetime.now(timezone.utc).isoformat())
                )
                connection.execute(
                    "UPDATE password_reset_tokens SET used_at = datetime('now') WHERE user_id = ? AND used_at IS NULL AND token_hash != ?",
                    (user["id"], token_hash)
                )
            reset_url = url_for("reset_password", token=token, _external=True)
            send_reset_email(user["email"], reset_url)

    return jsonify({
        "message": "If an account exists with this email, you'll receive a password reset link shortly."
    })


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = str(request.args.get("token", "") if request.method == "GET" else (request.get_json(silent=True) or request.form).get("token", ""))
    if request.method == "GET":
        if not token:
            return render_template("reset_password.html", valid=False)
        try:
            reset_serializer().loads(token, max_age=RESET_TOKEN_TTL)
        except (BadSignature, SignatureExpired):
            return render_template("reset_password.html", valid=False)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with get_db() as connection:
            record = connection.execute(
                "SELECT id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = ?",
                (token_hash,)
            ).fetchone()
        if not record or record["used_at"] or record["expires_at"] < int(time.time()):
            return render_template("reset_password.html", valid=False)
        return render_template("reset_password.html", valid=True, token=token)

    password = str((request.get_json(silent=True) or request.form).get("password", ""))
    confirm_password = str((request.get_json(silent=True) or request.form).get("confirm_password", ""))
    if password_errors(password):
        return jsonify({"error": " ".join(password_errors(password))}), 400
    if password != confirm_password:
        return jsonify({"error": "Password and confirmation do not match."}), 400
    if not token:
        return jsonify({"error": "This password reset link is invalid."}), 400
    try:
        payload = reset_serializer().loads(token, max_age=RESET_TOKEN_TTL)
    except (BadSignature, SignatureExpired):
        return jsonify({"error": "This password reset link is invalid or expired."}), 400

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_db() as connection:
        record = connection.execute(
            "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = ?",
            (token_hash,)
        ).fetchone()
        if not record or record["used_at"] or record["expires_at"] < int(time.time()):
            return jsonify({"error": "This password reset link is invalid or expired."}), 400
        if int(payload["uid"]) != record["user_id"]:
            return jsonify({"error": "This password reset link is invalid."}), 400
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(password), datetime.now(timezone.utc).isoformat(), record["user_id"])
        )
        connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), record["id"])
        )
    session.clear()
    return jsonify({"message": "Password changed successfully.", "redirect": url_for("signin")})


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return jsonify({"redirect": url_for("signin")})


@app.route("/profile")
@login_required
def profile():
    user = current_user()
    return render_template("profile.html", user={
        "name": user["full_name"],
        "email": user["email"],
        "created_at": user["created_at"]
    })


@app.route("/api/profile", methods=["GET", "PATCH"])
@login_required
def api_profile():
    user = current_user()
    if request.method == "GET":
        return jsonify({
            "name": user["full_name"],
            "email": user["email"],
            "created_at": user["created_at"]
        })
    payload = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", str(payload.get("name", "")).strip())[:100]
    if not name:
        return jsonify({"error": "Name cannot be empty."}), 400
    with get_db() as connection:
        connection.execute(
            "UPDATE users SET full_name = ?, updated_at = ? WHERE id = ?",
            (name, datetime.now(timezone.utc).isoformat(), user["id"])
        )
    return jsonify({"message": "Profile updated.", "name": name})


@app.route("/api/stats")
@login_required
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
@login_required
def api_files():
    workspace = current_workspace()
    metadata = load_metadata(workspace)
    return jsonify({
        "files": build_library(),
        "collections": sorted(metadata["collections"].keys(), key=str.lower)
    })


@app.route("/api/files/bulk", methods=["POST"])
@login_required
def bulk_update_files():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid request."}), 400

    paths = payload.get("paths")
    action = payload.get("action")
    if not isinstance(paths, list) or not paths:
        return jsonify({"error": "Select at least one file."}), 400
    if action not in {"favorite", "unfavorite", "add_tag", "remove_tag", "collection"}:
        return jsonify({"error": "Unsupported bulk action."}), 400

    workspace = current_workspace()
    metadata = load_metadata(workspace)
    file_metadata = metadata["files"]
    organized_folder = workspace / "organized"
    updated = 0
    for relative_path in paths:
        if not isinstance(relative_path, str):
            continue
        target = (organized_folder / relative_path).resolve()
        if not is_within_directory(organized_folder, target) or not target.is_file():
            continue
        item = file_metadata.setdefault(relative_path, {})
        if action == "favorite":
            item["favorite"] = True
        elif action == "unfavorite":
            item["favorite"] = False
        elif action == "add_tag":
            item["tags"] = clean_tags((item.get("tags", []) if isinstance(item.get("tags"), list) else []) + [payload.get("value")])
        elif action == "remove_tag":
            tag = payload.get("value")
            if isinstance(tag, str):
                item["tags"] = [existing for existing in item.get("tags", []) if existing != tag]
        elif action == "collection":
            collection = re.sub(r"\s+", " ", str(payload.get("value", "")).strip())[:50]
            old_collection = item.get("collection")
            if isinstance(old_collection, str) and old_collection in metadata["collections"]:
                members = metadata["collections"][old_collection]
                if relative_path in members:
                    members.remove(relative_path)
            item["collection"] = collection
            if collection:
                metadata["collections"].setdefault(collection, [])
                if relative_path not in metadata["collections"][collection]:
                    metadata["collections"][collection].append(relative_path)
            else:
                for members in metadata["collections"].values():
                    if relative_path in members:
                        members.remove(relative_path)
        updated += 1

    if not save_metadata(metadata, workspace):
        return jsonify({"error": "Could not save file metadata."}), 500
    return jsonify({"message": f"Updated {updated} file{'s' if updated != 1 else ''}.", "updated": updated})


@app.route("/api/history")
@login_required
def api_history():
    return jsonify({"history": load_history(current_workspace())})


@app.route("/files/download/<path:relative_path>")
@login_required
def download_library_file(relative_path):
    organized_folder = current_workspace() / "organized"
    target = (organized_folder / relative_path).resolve()
    if not is_within_directory(organized_folder, target) or not target.is_file():
        return jsonify({"error": "File not found."}), 404
    return send_file(
        target,
        as_attachment=True,
        download_name=target.name,
        mimetype="application/octet-stream"
    )


@app.route("/preview/<path:relative_path>")
@login_required
def preview_file(relative_path):
    organized_folder = current_workspace() / "organized"
    target = (organized_folder / relative_path).resolve()
    if not is_within_directory(organized_folder, target) or not target.is_file():
        return jsonify({"error": "File not found."}), 404
    extension = target.suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
        return send_from_directory(str(organized_folder), relative_path)
    if extension == ".pdf":
        response = send_file(target, mimetype="application/pdf", as_attachment=False)
        response.headers["Content-Disposition"] = f'inline; filename="{secure_filename(target.name)}"'
        response.headers["Cache-Control"] = "private, no-store"
        return response
    if extension in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".css", ".js", ".log"}:
        if target.stat().st_size > 2 * 1024 * 1024:
            return jsonify({"error": "Text preview is limited to 2 MB."}), 413
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = target.read_text(encoding="utf-8", errors="replace")
        response = jsonify({"filename": target.name, "content": content})
        response.headers["Cache-Control"] = "private, no-store"
        return response
    return jsonify({"error": "Preview is not available for this file type."}), 415


@app.route("/organize", methods=["POST"])
@login_required
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
    workspace = current_workspace()
    upload_folder = workspace / "uploads"
    organized_folder = workspace / "organized"
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
                persistent_upload = unique_path(upload_folder, filename)
                persistent_upload.write_bytes(content)
                persistent_destination = unique_path(organized_folder / category, filename)
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
    history = load_history(workspace)
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
    save_history(history, workspace)
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
