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
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from email.message import EmailMessage
from contextlib import contextmanager
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


@contextmanager
def get_db():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    with get_db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0,
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
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_lookup ON sessions(session_id, user_id);
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_email_verify_hash ON email_verification_tokens(token_hash);
        """)


init_database()


@app.before_request
def validate_csrf_and_origin():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin")
    if origin:
        allowed = {
            item.strip().rstrip("/")
            for item in os.environ.get("FILEFLOW_CORS_ORIGINS", "").split(",")
            if item.strip()
        }
        host_origin = f"{request.scheme}://{request.host}"
        allowed.add(host_origin.rstrip("/"))
        if origin.rstrip("/") not in allowed:
            return jsonify({"error": "Cross-origin request blocked."}), 403
    csrf_header = request.headers.get("X-CSRF-Token")
    if csrf_header and session.get("csrf_token") and not secrets.compare_digest(csrf_header, session["csrf_token"]):
        return jsonify({"error": "Invalid security token."}), 403
    return None


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    origin = request.headers.get("Origin")
    allowed_origins = {
        item.strip().rstrip("/")
        for item in os.environ.get("FILEFLOW_CORS_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin and origin.rstrip("/") in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        response.headers["Vary"] = "Origin"
    return response


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
    session_id = session.get("session_id")
    if not user_id or not session_id:
        return None
    with get_db() as connection:
        active = connection.execute(
            "SELECT id FROM sessions WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL AND expires_at > ?",
            (session_id, user_id, int(time.time()))
        ).fetchone()
        if not active:
            return None
        user = connection.execute(
            "SELECT id, full_name, email, email_verified, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return user


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


def create_server_session(user_id, remember):
    session_id = secrets.token_urlsafe(32)
    session["csrf_token"] = secrets.token_urlsafe(32)
    lifetime = 30 * 24 * 60 * 60 if remember else 12 * 60 * 60
    expires_at = int(time.time()) + lifetime
    with get_db() as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, expires_at, datetime.now(timezone.utc).isoformat())
        )
    return session_id, lifetime


def revoke_server_session():
    session_id = session.get("session_id")
    if session_id:
        with get_db() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_id = ?",
                (datetime.now(timezone.utc).isoformat(), session_id)
            )


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
        return {"files": {}, "collections": {}, "trash": {}}
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"files": {}, "collections": {}, "trash": {}}
        return {
            "files": data.get("files", {}) if isinstance(data.get("files"), dict) else {},
            "collections": data.get("collections", {}) if isinstance(data.get("collections"), dict) else {},
            "trash": data.get("trash", {}) if isinstance(data.get("trash"), dict) else {}
        }
    except (json.JSONDecodeError, OSError):
        return {"files": {}, "collections": {}, "trash": {}}


def valid_folder_name(value):
    if not isinstance(value, str):
        return None
    name = re.sub(r"\s+", " ", value.strip())
    if (
        not name
        or len(name) > 60
        or name in {".", ".."}
        or name.endswith(".")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*", name)
        or name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10))
        }
    ):
        return None
    return name


def update_metadata_path(metadata, old_path, new_path):
    item = metadata["files"].pop(old_path, {})
    metadata["files"][new_path] = item
    for members in metadata["collections"].values():
        if not isinstance(members, list):
            continue
        for index, member in enumerate(members):
            if member == old_path:
                members[index] = new_path
    return item


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
            parts = path.relative_to(organized_folder).parts
            relative_path = path.relative_to(organized_folder).as_posix()
            custom_folder = len(parts) > 2 and parts[0] == "Folders"
            category = get_category(path.name) if custom_folder or len(parts) == 1 else parts[0]
            item_metadata = file_metadata.get(relative_path, {})
            files.append({
                "filename": path.name,
                "category": category,
                "folder": "/".join(parts[:-1]) if custom_folder else "",
                "size": stat.st_size,
                "size_label": format_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "last_opened": item_metadata.get("last_opened", ""),
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
    metadata = load_metadata(workspace)
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

    folders_root = workspace / "organized" / "Folders"
    folder_count = sum(1 for path in folders_root.iterdir() if path.is_dir()) if folders_root.is_dir() else 0
    return {
        "total_files": len(organized),
        "uploaded_files": len(uploaded),
        "organized_files": len(organized),
        "storage_used": sum(file["size"] for file in organized),
        "storage_used_label": format_size(sum(file["size"] for file in organized)),
        "categories": len(category_counts),
        "folders": folder_count,
        "favorites": sum(1 for item in metadata["files"].values() if item.get("favorite") is True),
        "category_counts": category_counts,
        "category_sizes": category_sizes,
        "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: item[1], reverse=True)[:8]),
        "recent_uploaded": sorted(uploaded, key=lambda item: item["modified"], reverse=True)[:8],
        "recent_organized": organized[:8],
        "history": load_history(workspace)[:20]
    }


@app.route("/health")
def health():
    resend_configured = bool(os.environ.get("RESEND_API_KEY") and (
        os.environ.get("FILEFLOW_SMTP_FROM") or os.environ.get("FILEFLOW_EMAIL_FROM")
    ))
    smtp_host_configured = bool(os.environ.get("FILEFLOW_SMTP_HOST"))
    smtp_sender_configured = bool(
        os.environ.get("FILEFLOW_SMTP_FROM") or os.environ.get("FILEFLOW_SMTP_USERNAME")
    )
    smtp_configured = smtp_host_configured and smtp_sender_configured
    return jsonify({
        "status": "ok",
        "service": "FileFlow",
        "email_delivery": "resend" if resend_configured else ("smtp" if smtp_configured else "not_configured"),
        "smtp_host_configured": smtp_host_configured,
        "smtp_sender_configured": smtp_sender_configured,
        "smtp_username_configured": bool(os.environ.get("FILEFLOW_SMTP_USERNAME")),
        "smtp_password_configured": bool(os.environ.get("FILEFLOW_SMTP_PASSWORD"))
    })


@app.route("/auth.css")
def auth_styles():
    return send_from_directory(str(TEMPLATE_FOLDER), "auth.css")


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
@app.route("/api/auth/login", methods=["POST"])
def signin():
    if request.method in {"GET", "HEAD"}:
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
    session_id, lifetime = create_server_session(user["id"], remember)
    session["session_id"] = session_id
    session.permanent = remember
    return jsonify({
        "redirect": url_for("dashboard"),
        "name": user["full_name"],
        "csrf_token": session.get("csrf_token")
    })


@app.route("/signup", methods=["GET", "POST"])
@app.route("/api/auth/signup", methods=["POST"])
def signup():
    if request.method in {"GET", "HEAD"}:
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
    verification_token = verification_serializer().dumps({"uid": user_id, "nonce": secrets.token_urlsafe(24)})
    verification_hash = hashlib.sha256(verification_token.encode("utf-8")).hexdigest()
    with get_db() as connection:
        connection.execute(
            "INSERT INTO email_verification_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, verification_hash, int(time.time()) + 24 * 60 * 60, now)
        )
    send_verification_email(email, url_for("verify_email", token=verification_token, _external=True))
    session.clear()
    session["user_id"] = user_id
    session_id, lifetime = create_server_session(user_id, True)
    session["session_id"] = session_id
    session.permanent = True
    return jsonify({
        "redirect": url_for("dashboard"),
        "name": full_name,
        "csrf_token": session.get("csrf_token")
    })


def email_delivery_configured():
    return bool(os.environ.get("RESEND_API_KEY") or (
        os.environ.get("FILEFLOW_SMTP_HOST")
        and os.environ.get("FILEFLOW_SMTP_FROM")
    ))


def send_via_resend(to_email, subject, text_body, html_body):
    api_key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("FILEFLOW_SMTP_FROM") or os.environ.get("FILEFLOW_EMAIL_FROM")
    if not api_key or not sender:
        return False
    payload = json.dumps({
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": text_body,
        "html": html_body
    }).encode("utf-8")
    request_object = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(request_object, timeout=15) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        app.logger.error("Resend email delivery failed: %s", error)
        return False


def send_via_smtp(to_email, subject, text_body, html_body):
    host = os.environ.get("FILEFLOW_SMTP_HOST")
    port = int(os.environ.get("FILEFLOW_SMTP_PORT", "587"))
    username = os.environ.get("FILEFLOW_SMTP_USERNAME")
    password = os.environ.get("FILEFLOW_SMTP_PASSWORD")
    sender = os.environ.get("FILEFLOW_SMTP_FROM") or username
    use_tls = os.environ.get("FILEFLOW_SMTP_TLS", "true").lower() == "true"
    if not host or not sender:
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(text_body)
    message.set_alternative(html_body, subtype="html")
    try:
        smtp_class = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with smtp_class(host, port, timeout=15) as smtp:
            if port != 465 and use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError, TimeoutError) as error:
        app.logger.error("SMTP email delivery failed: %s", error)
        return False


def send_reset_email(email, reset_url):
    subject = "Reset your FileFlow password"
    text_body = (
        "We received a request to reset your FileFlow password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 30 minutes. If you did not request it, you can ignore this email."
    )
    html_body = f"""<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">FileFlow</h1><p>We received a request to reset your password.</p>
        <p><a href="{reset_url}" style="display:inline-block;padding:12px 18px;border-radius:8px;background:#6d7cf4;color:#fff;text-decoration:none;font-weight:bold">Reset password</a></p>
        <p style="color:#718096;font-size:13px">This link expires in 30 minutes. If you did not request it, you can ignore this email.</p></div></body></html>"""
    if send_via_resend(email, subject, text_body, html_body):
        return True
    if send_via_smtp(email, subject, text_body, html_body):
        return True
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
    message.set_alternative(
        f"""<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">FileFlow</h1><p>We received a request to reset your password.</p>
        <p><a href="{reset_url}" style="display:inline-block;padding:12px 18px;border-radius:8px;background:#6d7cf4;color:#fff;text-decoration:none;font-weight:bold">Reset password</a></p>
        <p style="color:#718096;font-size:13px">This link expires in 30 minutes. If you did not request it, you can ignore this email.</p></div></body></html>""",
        subtype="html"
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


def send_password_changed_email(email):
    subject = "Your FileFlow password was changed"
    text_body = "Your FileFlow password was changed. If you did not make this change, reset your password immediately."
    html_body = """<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">FileFlow security notice</h1><p>Your FileFlow password was changed.</p>
        <p style="color:#718096;font-size:13px">If you did not make this change, reset your password immediately.</p></div></body></html>"""
    if send_via_resend(email, subject, text_body, html_body):
        return True
    if send_via_smtp(email, subject, text_body, html_body):
        return True
    host = os.environ.get("FILEFLOW_SMTP_HOST")
    port = int(os.environ.get("FILEFLOW_SMTP_PORT", "587"))
    username = os.environ.get("FILEFLOW_SMTP_USERNAME")
    password = os.environ.get("FILEFLOW_SMTP_PASSWORD")
    sender = os.environ.get("FILEFLOW_SMTP_FROM", username)
    use_tls = os.environ.get("FILEFLOW_SMTP_TLS", "true").lower() == "true"
    if not host or not sender:
        return False
    message = EmailMessage()
    message["Subject"] = "Your FileFlow password was changed"
    message["From"] = sender
    message["To"] = email
    message.set_content("Your FileFlow password was changed. If you did not make this change, reset your password immediately.")
    message.set_alternative(
        """<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">FileFlow security notice</h1>
        <p>Your FileFlow password was changed.</p>
        <p style="color:#718096;font-size:13px">If you did not make this change, reset your password immediately.</p></div></body></html>""",
        subtype="html"
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
        app.logger.error("Password changed email failed: %s", error)
        return False


def verification_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="fileflow-email-verification")


def send_verification_email(email, verify_url):
    subject = "Verify your FileFlow email"
    text_body = f"Welcome to FileFlow.\n\nVerify your email address: {verify_url}\n\nThis verification link expires in 24 hours."
    html_body = f"""<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">Welcome to FileFlow</h1><p>Verify your email address to secure your account.</p>
        <p><a href="{verify_url}" style="display:inline-block;padding:12px 18px;border-radius:8px;background:#6d7cf4;color:#fff;text-decoration:none;font-weight:bold">Verify email</a></p>
        <p style="color:#718096;font-size:13px">This link expires in 24 hours.</p></div></body></html>"""
    if send_via_resend(email, subject, text_body, html_body):
        return True
    if send_via_smtp(email, subject, text_body, html_body):
        return True
    host = os.environ.get("FILEFLOW_SMTP_HOST")
    port = int(os.environ.get("FILEFLOW_SMTP_PORT", "587"))
    username = os.environ.get("FILEFLOW_SMTP_USERNAME")
    password = os.environ.get("FILEFLOW_SMTP_PASSWORD")
    sender = os.environ.get("FILEFLOW_SMTP_FROM", username)
    use_tls = os.environ.get("FILEFLOW_SMTP_TLS", "true").lower() == "true"
    if not host or not sender:
        app.logger.warning("Email verification requested but SMTP is not configured.")
        return False
    message = EmailMessage()
    message["Subject"] = "Verify your FileFlow email"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Welcome to FileFlow.\n\nVerify your email address: {verify_url}\n\n"
        "This verification link expires in 24 hours."
    )
    message.set_alternative(
        f"""<html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif">
        <div style="max-width:560px;margin:32px auto;padding:28px;background:#fff;border-radius:16px;border:1px solid #e4e8f1">
        <h1 style="color:#172033">Welcome to FileFlow</h1><p>Verify your email address to secure your account.</p>
        <p><a href="{verify_url}" style="display:inline-block;padding:12px 18px;border-radius:8px;background:#6d7cf4;color:#fff;text-decoration:none;font-weight:bold">Verify email</a></p>
        <p style="color:#718096;font-size:13px">This link expires in 24 hours.</p></div></body></html>""",
        subtype="html"
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
        app.logger.error("Verification email failed: %s", error)
        return False


@app.route("/forgot-password", methods=["GET", "POST"])
@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    if request.method in {"GET", "HEAD"}:
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
            if not send_reset_email(user["email"], reset_url):
                app.logger.warning("Password reset delivery failed for user id %s.", user["id"])

    return jsonify({
        "message": "If an account exists with this email, you'll receive a password reset link shortly."
    })


@app.route("/reset-password", methods=["GET", "POST"])
@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    token = str(request.args.get("token", "") if request.method == "GET" else (request.get_json(silent=True) or request.form).get("token", ""))
    if request.method in {"GET", "HEAD"}:
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
        connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), record["user_id"])
        )
        reset_user = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (record["user_id"],)
        ).fetchone()
    if reset_user:
        send_password_changed_email(reset_user["email"])
    session.clear()
    return jsonify({"message": "Password changed successfully.", "redirect": "/signin"})


@app.route("/verify-email", methods=["GET", "POST"])
@app.route("/api/auth/verify-email", methods=["POST"])
def verify_email():
    token = str(request.args.get("token", "") if request.method == "GET" else (request.get_json(silent=True) or request.form).get("token", ""))
    if request.method in {"GET", "HEAD"}:
        if not token:
            return render_template("verify_email.html", state="invalid")
        try:
            payload = verification_serializer().loads(token, max_age=24 * 60 * 60)
        except (BadSignature, SignatureExpired):
            return render_template("verify_email.html", state="invalid")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with get_db() as connection:
            record = connection.execute(
                "SELECT id, user_id, expires_at, used_at FROM email_verification_tokens WHERE token_hash = ?",
                (token_hash,)
            ).fetchone()
            if not record or record["used_at"] or record["expires_at"] < int(time.time()) or int(payload["uid"]) != record["user_id"]:
                return render_template("verify_email.html", state="invalid")
            connection.execute(
                "UPDATE users SET email_verified = 1, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), record["user_id"])
            )
            connection.execute(
                "UPDATE email_verification_tokens SET used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), record["id"])
            )
        return render_template("verify_email.html", state="success")

    payload = request.get_json(silent=True) or request.form
    token = str(payload.get("token", ""))
    return redirect(url_for("verify_email", token=token))


@app.route("/api/auth/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    user = current_user()
    if user is None:
        return jsonify({"error": "Sign in to resend verification."}), 401
    token = verification_serializer().dumps({"uid": user["id"], "nonce": secrets.token_urlsafe(24)})
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_db() as connection:
        connection.execute(
            "UPDATE email_verification_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), user["id"])
        )
        connection.execute(
            "INSERT INTO email_verification_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], token_hash, int(time.time()) + 24 * 60 * 60, datetime.now(timezone.utc).isoformat())
        )
    send_verification_email(user["email"], url_for("verify_email", token=token, _external=True))
    return jsonify({"message": "If an account exists, a verification email will be sent."})


@app.route("/api/auth/me")
@login_required
def api_auth_me():
    user = current_user()
    return jsonify({
        "id": user["id"],
        "name": user["full_name"],
        "email": user["email"],
        "created_at": user["created_at"],
        "csrf_token": session.get("csrf_token")
    })


@app.route("/logout", methods=["POST"])
@app.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    revoke_server_session()
    session.clear()
    return jsonify({"redirect": "/signin"})


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


@app.route("/api/folders", methods=["GET", "POST"])
@login_required
def api_folders():
    workspace = current_workspace()
    organized_folder = workspace / "organized"
    folders_root = organized_folder / "Folders"
    if request.method == "GET":
        folders = []
        if folders_root.is_dir():
            for path in sorted(folders_root.iterdir(), key=lambda item: item.name.lower()):
                if not path.is_dir() or not is_within_directory(folders_root, path):
                    continue
                files = [item for item in path.rglob("*") if item.is_file() and is_within_directory(path, item)]
                folders.append({
                    "name": path.name,
                    "files": len(files),
                    "size": sum(item.stat().st_size for item in files)
                })
        return jsonify({"folders": folders})

    payload = request.get_json(silent=True) or {}
    name = valid_folder_name(payload.get("name"))
    if not name:
        return jsonify({"error": "Use a folder name with letters, numbers, spaces, dots, dashes or underscores."}), 400
    folders_root.mkdir(parents=True, exist_ok=True)
    if any(path.name.lower() == name.lower() for path in folders_root.iterdir() if path.is_dir()):
        return jsonify({"error": "A folder with that name already exists."}), 409
    try:
        (folders_root / name).mkdir()
    except OSError:
        app.logger.exception("Could not create a workspace folder.")
        return jsonify({"error": "Could not create the folder."}), 500
    return jsonify({"message": "Folder created.", "name": name}), 201


@app.route("/api/folders/<path:folder_name>", methods=["PATCH", "DELETE"])
@login_required
def manage_folder(folder_name):
    old_name = valid_folder_name(folder_name)
    if not old_name:
        return jsonify({"error": "Invalid folder name."}), 400
    workspace = current_workspace()
    folders_root = workspace / "organized" / "Folders"
    source = (folders_root / old_name).resolve()
    if not is_within_directory(folders_root, source) or not source.is_dir():
        return jsonify({"error": "Folder not found."}), 404

    if request.method == "DELETE":
        try:
            source.rmdir()
        except OSError:
            return jsonify({"error": "Move or remove the folder's files before deleting it."}), 409
        return jsonify({"message": "Folder deleted."})

    payload = request.get_json(silent=True) or {}
    new_name = valid_folder_name(payload.get("name"))
    if not new_name:
        return jsonify({"error": "Use a folder name with letters, numbers, spaces, dots, dashes or underscores."}), 400
    if new_name.lower() == old_name.lower():
        return jsonify({"message": "Folder name unchanged.", "name": old_name})
    destination = folders_root / new_name
    if destination.exists():
        return jsonify({"error": "A folder with that name already exists."}), 409
    try:
        source.rename(destination)
    except OSError:
        app.logger.exception("Could not rename a workspace folder.")
        return jsonify({"error": "Could not rename the folder."}), 500

    old_prefix = f"Folders/{old_name}/"
    new_prefix = f"Folders/{new_name}/"
    metadata = load_metadata(workspace)
    for old_path in list(metadata["files"]):
        if old_path.startswith(old_prefix):
            metadata["files"][new_prefix + old_path[len(old_prefix):]] = metadata["files"].pop(old_path)
    for members in metadata["collections"].values():
        if isinstance(members, list):
            for index, member in enumerate(members):
                if isinstance(member, str) and member.startswith(old_prefix):
                    members[index] = new_prefix + member[len(old_prefix):]
    if not save_metadata(metadata, workspace):
        try:
            destination.rename(source)
        except OSError:
            app.logger.exception("Could not roll back a folder rename after metadata save failed.")
        return jsonify({"error": "Could not save folder metadata."}), 500
    return jsonify({"message": "Folder renamed.", "name": new_name})


@app.route("/api/trash")
@login_required
def api_trash():
    workspace = current_workspace()
    metadata = load_metadata(workspace)
    trash_root = workspace / "trash"
    files = []
    for trash_id, item in metadata["trash"].items():
        if not isinstance(item, dict):
            continue
        stored_path = item.get("stored_path", "")
        target = (workspace / stored_path).resolve()
        if not is_within_directory(trash_root, target) or not target.is_file():
            continue
        files.append({
            "id": trash_id,
            "filename": item.get("filename", target.name),
            "original_path": item.get("original_path", ""),
            "deleted_at": item.get("deleted_at", ""),
            "size": target.stat().st_size,
            "size_label": format_size(target.stat().st_size)
        })
    files.sort(key=lambda item: item["deleted_at"], reverse=True)
    return jsonify({"files": files})


@app.route("/api/trash/<trash_id>", methods=["POST", "DELETE"])
@login_required
def manage_trashed_file(trash_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16}", trash_id):
        return jsonify({"error": "File not found in trash."}), 404
    workspace = current_workspace()
    metadata = load_metadata(workspace)
    item = metadata["trash"].get(trash_id)
    if not isinstance(item, dict):
        return jsonify({"error": "File not found in trash."}), 404
    trash_root = workspace / "trash"
    target = (workspace / item.get("stored_path", "")).resolve()
    if not is_within_directory(trash_root, target) or not target.is_file():
        return jsonify({"error": "File not found in trash."}), 404

    if request.method == "DELETE":
        try:
            target.unlink()
        except OSError:
            app.logger.exception("Could not permanently delete a trashed file.")
            return jsonify({"error": "Could not delete the file."}), 500
        del metadata["trash"][trash_id]
        if not save_metadata(metadata, workspace):
            return jsonify({"error": "File was deleted, but its trash record could not be updated."}), 500
        return jsonify({"message": "File permanently deleted."})

    original_path = item.get("original_path", "")
    organized_root = workspace / "organized"
    destination = (organized_root / original_path).resolve()
    if not original_path or not is_within_directory(organized_root, destination):
        return jsonify({"error": "The original file location is invalid."}), 400
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = unique_path(destination.parent, destination.name)
    try:
        target.replace(destination)
    except OSError:
        app.logger.exception("Could not restore a trashed file.")
        return jsonify({"error": "Could not restore the file."}), 500
    metadata["files"][destination.relative_to(organized_root).as_posix()] = item.get("file_metadata", {})
    file_meta = metadata["files"][destination.relative_to(organized_root).as_posix()]
    collection = file_meta.get("collection")
    if isinstance(collection, str) and collection:
        members = metadata["collections"].setdefault(collection, [])
        if destination.relative_to(organized_root).as_posix() not in members:
            members.append(destination.relative_to(organized_root).as_posix())
    del metadata["trash"][trash_id]
    if not save_metadata(metadata, workspace):
        return jsonify({"error": "File was restored, but its metadata could not be saved."}), 500
    return jsonify({"message": "File restored."})


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
    if action not in {"favorite", "unfavorite", "add_tag", "remove_tag", "collection", "move", "trash"}:
        return jsonify({"error": "Unsupported bulk action."}), 400

    workspace = current_workspace()
    metadata = load_metadata(workspace)
    file_metadata = metadata["files"]
    organized_folder = workspace / "organized"
    destination_folder = None
    if action == "move":
        folder_name = valid_folder_name(payload.get("value"))
        if not folder_name:
            return jsonify({"error": "Choose a valid destination folder."}), 400
        destination_folder = (organized_folder / "Folders" / folder_name).resolve()
        if not is_within_directory(organized_folder, destination_folder) or not destination_folder.is_dir():
            return jsonify({"error": "Destination folder not found."}), 404
    updated = 0
    for relative_path in paths:
        if not isinstance(relative_path, str):
            continue
        target = (organized_folder / relative_path).resolve()
        if not is_within_directory(organized_folder, target) or not target.is_file():
            continue
        if action == "move":
            destination = unique_path(destination_folder, target.name)
            try:
                target.replace(destination)
            except OSError:
                app.logger.exception("Could not move a workspace file.")
                continue
            update_metadata_path(
                metadata,
                relative_path,
                destination.relative_to(organized_folder).as_posix()
            )
            updated += 1
            continue
        if action == "trash":
            trash_root = workspace / "trash"
            trash_root.mkdir(parents=True, exist_ok=True)
            trash_id = secrets.token_urlsafe(12)
            trashed_path = trash_root / f"{trash_id}{target.suffix}"
            try:
                target.replace(trashed_path)
            except OSError:
                app.logger.exception("Could not move a workspace file to trash.")
                continue
            item_metadata = file_metadata.pop(relative_path, {})
            for members in metadata["collections"].values():
                if isinstance(members, list) and relative_path in members:
                    members.remove(relative_path)
            metadata["trash"][trash_id] = {
                "filename": target.name,
                "original_path": relative_path,
                "stored_path": trashed_path.relative_to(workspace).as_posix(),
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "file_metadata": item_metadata
            }
            updated += 1
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
