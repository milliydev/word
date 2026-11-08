#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KompSavod – bitta faylli server: backend (Python/Flask) + frontend (React).

Ishga tushirish:
    pip install flask waitress
    python server.py

Keyin brauzerda oching: http://localhost:8000

Sozlamalar (muhit o'zgaruvchilari, ixtiyoriy):
    ADMIN_PHONE     Admin telefon raqami(lari), vergul bilan: +998901234567,+998935554433
                    Shu raqam bilan kirgan odam avtomatik admin bo'ladi.
    ADMIN_LOGIN     Admin panel (/admin) logini (standart: admin)
    ADMIN_PASSWORD  Admin panel paroli (berilmasa – data/.admin_password faylida avtomatik yaratiladi)
    ESKIZ_EMAIL     Eskiz.uz akkaunt emaili   (berilmasa – SINOV REJIMI: kod terminalda chiqadi)
    ESKIZ_PASSWORD  Eskiz.uz paroli (kabinetdagi API kaliti)
    ESKIZ_FROM      Yuboruvchi nomi (standart: 4546)
    SMS_TEXT        SMS matni, {code} o'rniga kod qo'yiladi (Eskiz'da tasdiqlangan shablon bilan bir xil bo'lsin)
    PORT            Port (standart: 8000)
    DATA_DIR        Baza va zaxira nusxalar papkasi (standart: server.py yonidagi "data")
    HTTPS           1 – sayt https orqali ishlasa (cookie xavfsizligi uchun)
    TRUST_PROXY     1 – server Nginx/Cloudflare ortida bo'lsa (haqiqiy IP manzil uchun)
"""

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

# ------------------------------------------------------------------
# Sozlamalar
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kompsavod.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
SECRET_FILE = os.path.join(DATA_DIR, ".secret_key")
BACKUP_EVERY_HOURS = 6
BACKUP_KEEP = 60  # oxirgi 60 ta nusxa saqlanadi (~15 kun)

ESKIZ_EMAIL = os.environ.get("ESKIZ_EMAIL", "").strip()
ESKIZ_PASSWORD = os.environ.get("ESKIZ_PASSWORD", "").strip()
ESKIZ_FROM = os.environ.get("ESKIZ_FROM", "4546").strip()
ESKIZ_URL = "https://notify.eskiz.uz/api"
SMS_TEXT = os.environ.get("SMS_TEXT", "KompSavod: tasdiqlash kodi {code}. Kodni hech kimga bermang.")
DEV_MODE = not (ESKIZ_EMAIL and ESKIZ_PASSWORD)

OTP_TTL_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
REGISTER_WINDOW_SECONDS = 15 * 60

REGIONS = ["Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Farg‘ona", "Jizzax", "Xorazm",
           "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog‘iston", "Samarqand", "Sirdaryo", "Surxondaryo",
           "Chet elda"]
STATUSES = {"pending", "approved", "rejected", "blocked"}

os.makedirs(BACKUP_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kompsavod")


def _load_secret():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _load_secret()


def _normalize_phone(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 9:
        d = "998" + d
    if len(d) == 12 and d.startswith("998"):
        return "+" + d
    return None


ADMIN_PHONES = {p for p in (_normalize_phone(x) for x in os.environ.get("ADMIN_PHONE", "").split(",")) if p}

# Admin panelga login + parol bilan kirish (http://localhost:8000/admin)
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin").strip() or "admin"
ADMIN_PASSWORD_FILE = os.path.join(DATA_DIR, ".admin_password")


def _load_admin_password():
    env = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env:
        return env
    if os.path.exists(ADMIN_PASSWORD_FILE):
        with open(ADMIN_PASSWORD_FILE) as f:
            saved = f.read().strip()
        if saved:
            return saved
    pwd = secrets.token_urlsafe(9)
    with open(ADMIN_PASSWORD_FILE, "w") as f:
        f.write(pwd)
    try:
        os.chmod(ADMIN_PASSWORD_FILE, 0o600)
    except OSError:
        pass
    return pwd


ADMIN_PASSWORD = _load_admin_password()
ADMIN_LOGIN_MAX_FAILS = 10          # 15 daqiqada bitta IP uchun
ADMIN_LOGIN_WINDOW_SECONDS = 15 * 60
_admin_login_fails = {}
_admin_login_lock = threading.Lock()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",
    SESSION_COOKIE_NAME="ks_session",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=1024 * 1024,
    JSON_AS_ASCII=False,
)

# ------------------------------------------------------------------
# Ma'lumotlar bazasi
# ------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    name TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    status_changed_at TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    title TEXT NOT NULL,
    video_url TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 1,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_courses (
    user_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    granted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, course_id)
);
CREATE TABLE IF NOT EXISTS logins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS otp_codes (
    phone TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS otp_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    ip TEXT,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_id);
CREATE INDEX IF NOT EXISTS idx_logins_user ON logins(user_id);
CREATE INDEX IF NOT EXISTS idx_otp_req ON otp_requests(phone, at);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "email" not in cols:
        # Eski baza: email + parol bilan ro'yxatdan o'tish uchun users jadvalini yangilash
        conn.close()
        backup_now()
        conn = connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript("""
BEGIN;
CREATE TABLE users_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    name TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    status_changed_at TEXT
);
INSERT INTO users_new (id, phone, name, region, role, status, admin_note, created_at, last_login_at, status_changed_at)
    SELECT id, phone, name, region, role, status, admin_note, created_at, last_login_at, status_changed_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
COMMIT;
""")
        conn.execute("PRAGMA foreign_keys = ON")
        log.info("Baza yangilandi: email va parol ustunlari qo'shildi")
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------
# Zaxira nusxa (backup)
# ------------------------------------------------------------------
def backup_now():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"kompsavod-{stamp}.db")
    src = connect()
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("kompsavod-") and f.endswith(".db"))
    for old in files[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    log.info("Zaxira nusxa olindi: %s", dest)


def _backup_loop():
    while True:
        try:
            backup_now()
        except Exception:
            log.exception("Zaxira nusxa olishda xatolik")
        time.sleep(BACKUP_EVERY_HOURS * 3600)


# ------------------------------------------------------------------
# SMS (Eskiz.uz)
# ------------------------------------------------------------------
_eskiz_token = {"value": None, "at": 0.0}
_eskiz_lock = threading.Lock()


def _eskiz_login():
    data = urllib.parse.urlencode({"email": ESKIZ_EMAIL, "password": ESKIZ_PASSWORD}).encode()
    req = urllib.request.Request(f"{ESKIZ_URL}/auth/login", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode())
    token = (body.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"Eskiz login javobi kutilmagan: {body}")
    _eskiz_token.update(value=token, at=time.time())
    return token


def _eskiz_send(phone, text, retry=True):
    with _eskiz_lock:
        token = _eskiz_token["value"]
        if not token or time.time() - _eskiz_token["at"] > 20 * 24 * 3600:
            token = _eskiz_login()
    data = urllib.parse.urlencode({
        "mobile_phone": phone.lstrip("+"),
        "message": text,
        "from": ESKIZ_FROM,
    }).encode()
    req = urllib.request.Request(f"{ESKIZ_URL}/message/sms/send", data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            log.info("Eskiz javobi: %s", body[:300])
            return True
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:
            with _eskiz_lock:
                _eskiz_token["value"] = None
            return _eskiz_send(phone, text, retry=False)
        log.error("Eskiz xatosi %s: %s", e.code, e.read().decode(errors="replace")[:300])
        return False


def send_sms(phone, code):
    text = SMS_TEXT.replace("{code}", code)
    if DEV_MODE:
        print(f"\n===== SINOV REJIMI: {phone} uchun kod: {code} =====\n", flush=True)
        return True
    try:
        return _eskiz_send(phone, text)
    except Exception:
        log.exception("SMS yuborib bo'lmadi")
        return False


# ------------------------------------------------------------------
# Yordamchilar
# ------------------------------------------------------------------
def client_ip():
    if os.environ.get("TRUST_PROXY") == "1":
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def err(message, code=400):
    return jsonify({"error": message}), code


def body_json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def clean_text(value, max_len):
    return str(value or "").strip()[:max_len]


def to_int(value, default=0, lo=0, hi=9999):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


VIDEO_RE = re.compile(
    r"^https?://(?:(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)[\w-]{11}"
    r"|(?:www\.|player\.)?vimeo\.com/(?:video/)?\d+)"
)


def code_hash(phone, code):
    return hmac.new(SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def public_user(row):
    if row is None:
        return None
    return {
        "id": row["id"], "name": row["name"], "phone": row["phone"], "email": row["email"], "region": row["region"],
        "role": row["role"], "status": row["status"],
        "createdAt": row["created_at"], "lastLoginAt": row["last_login_at"],
    }


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def audit(admin_id, action, target, details=None):
    get_db().execute("INSERT INTO audit_log (admin_id, action, target, details, at) VALUES (?,?,?,?,?)",
                     (admin_id, action, str(target), json.dumps(details or {}, ensure_ascii=False), now_iso()))


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if user is None:
            return err("Avval tizimga kiring.", 401)
        g.user = user
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if user is None:
            return err("Avval tizimga kiring.", 401)
        if user["role"] != "admin":
            return err("Bu amal faqat admin uchun.", 403)
        g.user = user
        return fn(*a, **kw)
    return wrapper


@app.before_request
def _csrf_guard():
    # Boshqa saytlardan soxta so'rovlarning oldini olish
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Requested-With") != "kompsavod":
            return err("Ruxsat etilmagan so‘rov.", 403)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _record_login(user_id):
    db = get_db()
    ts = now_iso()
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (ts, user_id))
    db.execute("INSERT INTO logins (user_id, at, ip, user_agent) VALUES (?,?,?,?)",
               (user_id, ts, client_ip(), request.headers.get("User-Agent", "")[:300]))


def _ensure_admin(user):
    if user["phone"] in ADMIN_PHONES and (user["role"] != "admin" or user["status"] != "approved"):
        get_db().execute("UPDATE users SET role = 'admin', status = 'approved' WHERE id = ?", (user["id"],))


# ------------------------------------------------------------------
# API: autentifikatsiya
# ------------------------------------------------------------------
@app.get("/api/me")
def api_me():
    user = current_user()
    return jsonify({"user": public_user(user), "devMode": DEV_MODE})


@app.post("/api/auth/send-code")
def api_send_code():
    phone = _normalize_phone(body_json().get("phone"))
    if not phone:
        return err("Telefon raqam noto‘g‘ri. +998 dan keyin 9 ta raqam kiriting.")
    db = get_db()
    now = time.time()
    ip = client_ip()
    last_min = db.execute("SELECT COUNT(*) FROM otp_requests WHERE phone = ? AND at > ?", (phone, now - 60)).fetchone()[0]
    if last_min:
        return err("Kod yaqinda yuborildi. 1 daqiqadan keyin qayta so‘rang.", 429)
    last_hour = db.execute("SELECT COUNT(*) FROM otp_requests WHERE phone = ? AND at > ?", (phone, now - 3600)).fetchone()[0]
    if last_hour >= 5:
        return err("Bu raqamga juda ko‘p kod so‘raldi. 1 soatdan keyin urinib ko‘ring.", 429)
    ip_hour = db.execute("SELECT COUNT(*) FROM otp_requests WHERE ip = ? AND at > ?", (ip, now - 3600)).fetchone()[0]
    if ip_hour >= 20:
        return err("Juda ko‘p urinish bo‘ldi. Keyinroq urinib ko‘ring.", 429)

    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.execute("INSERT OR REPLACE INTO otp_codes (phone, code_hash, expires_at, attempts) VALUES (?,?,?,0)",
               (phone, code_hash(phone, code), now + OTP_TTL_SECONDS))
    db.execute("INSERT INTO otp_requests (phone, ip, at) VALUES (?,?,?)", (phone, ip, now))
    db.execute("DELETE FROM otp_requests WHERE at < ?", (now - 86400,))  # 1 kundan eski texnik yozuvlar
    db.commit()
    if not send_sms(phone, code):
        return err("SMS yuborib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.", 502)
    return jsonify({"ok": True, "devMode": DEV_MODE})


@app.post("/api/auth/verify")
def api_verify():
    data = body_json()
    phone = _normalize_phone(data.get("phone"))
    code = re.sub(r"\D", "", str(data.get("code") or ""))
    if not phone or len(code) != 6:
        return err("Kod 6 ta raqamdan iborat.")
    db = get_db()
    row = db.execute("SELECT * FROM otp_codes WHERE phone = ?", (phone,)).fetchone()
    if row is None or row["expires_at"] < time.time():
        return err("Kodning muddati tugagan. Yangi kod so‘rang.")
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        db.execute("DELETE FROM otp_codes WHERE phone = ?", (phone,))
        db.commit()
        return err("Juda ko‘p noto‘g‘ri urinish. Yangi kod so‘rang.")
    if not hmac.compare_digest(row["code_hash"], code_hash(phone, code)):
        db.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE phone = ?", (phone,))
        db.commit()
        return err("Kod noto‘g‘ri. SMS’dagi 6 xonali kodni kiriting.")

    db.execute("DELETE FROM otp_codes WHERE phone = ?", (phone,))
    session.clear()
    session.permanent = True
    user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    if user is None:
        session["verified_phone"] = phone
        session["verified_at"] = time.time()
        db.commit()
        return jsonify({"needProfile": True})
    _ensure_admin(user)
    _record_login(user["id"])
    db.commit()
    session["uid"] = user["id"]
    return jsonify({"user": public_user(db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())})


@app.post("/api/auth/register")
def api_register():
    phone = session.get("verified_phone")
    if not phone or time.time() - session.get("verified_at", 0) > REGISTER_WINDOW_SECONDS:
        return err("Telefon raqamni qayta tasdiqlang.", 401)
    data = body_json()
    name = re.sub(r"\s+", " ", clean_text(data.get("name"), 80))
    region = clean_text(data.get("region"), 60)
    if len(name) < 2:
        return err("Ism va familiyangizni yozing.")
    if region not in REGIONS:
        return err("Hududni ro‘yxatdan tanlang.")
    db = get_db()
    is_admin = phone in ADMIN_PHONES
    try:
        cur = db.execute(
            "INSERT INTO users (phone, name, region, role, status, created_at) VALUES (?,?,?,?,?,?)",
            (phone, name, region, "admin" if is_admin else "student", "approved" if is_admin else "pending", now_iso()))
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        uid = db.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()["id"]
    _record_login(uid)
    db.commit()
    session.pop("verified_phone", None)
    session.pop("verified_at", None)
    session["uid"] = uid
    return jsonify({"user": public_user(db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())})


@app.post("/api/auth/admin-login")
def api_admin_login():
    data = body_json()
    login = str(data.get("login") or "").strip()
    password = str(data.get("password") or "").strip()  # nusxalashda ilashib qolgan bo'sh joylar
    ip = client_ip()
    now = time.time()
    with _admin_login_lock:
        fails = [t for t in _admin_login_fails.get(ip, []) if t > now - ADMIN_LOGIN_WINDOW_SECONDS]
        _admin_login_fails[ip] = fails
        if len(fails) >= ADMIN_LOGIN_MAX_FAILS:
            return err("Juda ko‘p noto‘g‘ri urinish. 15 daqiqadan keyin qayta urinib ko‘ring.", 429)
    ok_login = hmac.compare_digest(login.encode(), ADMIN_LOGIN.encode())
    ok_pass = hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
    if not (ok_login and ok_pass):
        with _admin_login_lock:
            _admin_login_fails.setdefault(ip, []).append(now)
        log.warning("Admin panelga noto‘g‘ri kirish urinishi, IP: %s", ip)
        return err("Login yoki parol noto‘g‘ri.", 401)
    with _admin_login_lock:
        _admin_login_fails.pop(ip, None)

    db = get_db()
    phone = sorted(ADMIN_PHONES)[0] if ADMIN_PHONES else "admin"
    user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    if user is None:
        cur = db.execute(
            "INSERT INTO users (phone, name, region, role, status, created_at) VALUES (?,?,?,?,?,?)",
            (phone, "Admin", "", "admin", "approved", now_iso()))
        uid = cur.lastrowid
    else:
        uid = user["id"]
        db.execute("UPDATE users SET role = 'admin', status = 'approved' WHERE id = ?", (uid,))
    _record_login(uid)
    db.commit()
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return jsonify({"user": public_user(db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())})


# ---------- Email + parol bilan ro'yxatdan o'tish va kirish ----------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_auth_hits = {}
_auth_lock = threading.Lock()


def _rl_blocked(key, limit, window):
    now = time.time()
    with _auth_lock:
        hits = [t for t in _auth_hits.get(key, []) if t > now - window]
        _auth_hits[key] = hits
        return len(hits) >= limit


def _rl_hit(key):
    with _auth_lock:
        _auth_hits.setdefault(key, []).append(time.time())


def _normalize_email(raw):
    e = str(raw or "").strip().lower()
    return e if len(e) <= 120 and EMAIL_RE.match(e) else None


def _start_session(uid):
    _record_login(uid)
    get_db().commit()
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return jsonify({"user": public_user(get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())})


@app.post("/api/auth/signup")
def api_signup():
    data = body_json()
    name = re.sub(r"\s+", " ", clean_text(data.get("name"), 80))
    region = clean_text(data.get("region"), 60)
    email = _normalize_email(data.get("email"))
    password = str(data.get("password") or "")
    if len(name) < 2:
        return err("Ism va familiyangizni yozing.")
    if region not in REGIONS:
        return err("Hududni ro‘yxatdan tanlang.")
    if not email:
        return err("Email manzil noto‘g‘ri. Masalan: ism@gmail.com")
    if len(password) < 6:
        return err("Parol kamida 6 ta belgidan iborat bo‘lsin.")
    if len(password) > 128:
        return err("Parol juda uzun.")
    key = "signup:" + client_ip()
    if _rl_blocked(key, 10, 3600):
        return err("Juda ko‘p urinish bo‘ldi. Keyinroq urinib ko‘ring.", 429)
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return err("Bu email bilan avval ro‘yxatdan o‘tilgan. «Kirish» bo‘limidan kiring.", 409)
    try:
        cur = db.execute(
            "INSERT INTO users (email, password_hash, name, region, role, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (email, generate_password_hash(password, method="pbkdf2:sha256:600000"), name, region, "student", "pending", now_iso()))
    except sqlite3.IntegrityError:
        return err("Bu email bilan avval ro‘yxatdan o‘tilgan. «Kirish» bo‘limidan kiring.", 409)
    _rl_hit(key)
    return _start_session(cur.lastrowid)


@app.post("/api/auth/login")
def api_login():
    data = body_json()
    email = _normalize_email(data.get("email"))
    password = str(data.get("password") or "")
    key = "login:" + client_ip()
    if _rl_blocked(key, 10, 15 * 60):
        return err("Juda ko‘p noto‘g‘ri urinish. 15 daqiqadan keyin qayta urinib ko‘ring.", 429)
    row = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone() if email else None
    if row is None or not row["password_hash"] or not check_password_hash(row["password_hash"], password):
        _rl_hit(key)
        return err("Email yoki parol noto‘g‘ri.", 401)
    return _start_session(row["id"])


# ---------- O'qituvchi haqida ma'lumot ----------
TEACHER_DEFAULTS = {
    "name": "Zohid Tojiyev",
    "title": "Dasturchi, PDP Junior o‘qituvchisi",
    "bio": "3 yildan ortiq tajribaga ega dasturchi. Hozirda PDP Juniorda o‘qituvchilik qiladi va 100 dan ortiq o‘quvchiga dars bergan.",
    "years": "3+",
    "students": "100+",
    "show": "1",
}
DEFAULT_TEACHER_PHOTO = os.path.join(BASE_DIR, "static", "teacher.png")
TEACHER_LIMITS = {"name": 80, "title": 120, "bio": 1200, "years": 20, "students": 20}
PHOTO_TYPES = {b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"\x89PNG": ("png", "image/png")}


def _settings(prefix):
    rows = get_db().execute("SELECT key, value FROM settings WHERE key LIKE ?", (prefix + "%",)).fetchall()
    return {r["key"][len(prefix):]: r["value"] for r in rows}


def teacher_dict():
    saved = _settings("teacher.")
    t = {k: saved.get(k, v) for k, v in TEACHER_DEFAULTS.items()}
    t["show"] = t["show"] == "1"
    photo = saved.get("photo")
    if photo:
        t["photoUrl"] = f"/teacher-photo?v={saved.get('photo_v', '0')}"
    else:
        # Admin hali rasm yuklamagan bo'lsa – kod bilan birga keladigan standart rasm
        t["photoUrl"] = "/teacher-photo?v=default" if os.path.exists(DEFAULT_TEACHER_PHOTO) else None
    return t


SOCIAL_DEFAULTS = {
    "instagram": "https://www.instagram.com/zohid_tojiyev",
    "telegram": "https://t.me/tojiyev_work",
    "youtube": "https://www.youtube.com/@Zohid_Tojiyev",
    "phone": "",
}
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")


def _social_url(kind, raw):
    """Foydalanuvchi yozgan @username yoki havolani toza https havolaga aylantiradi. Xato bo'lsa None."""
    v = str(raw or "").strip()
    if not v:
        return ""
    if kind == "phone":
        return v[:30] if re.fullmatch(r"[+\d\s()-]{5,30}", v) else None
    hosts = {"instagram": ("instagram.com",), "telegram": ("t.me", "telegram.me"), "youtube": ("youtube.com", "youtu.be")}[kind]
    if v.startswith("@") or _HANDLE_RE.match(v):
        h = v.lstrip("@")
        if not _HANDLE_RE.match(h):
            return None
        return {"instagram": f"https://www.instagram.com/{h}", "telegram": f"https://t.me/{h}",
                "youtube": f"https://www.youtube.com/@{h}"}[kind]
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    try:
        u = urllib.parse.urlsplit(v)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    if host not in hosts:
        return None
    path = re.sub(r"/+$", "", u.path)
    if not path:
        return None
    query = ("?" + u.query) if kind == "youtube" and u.query else ""
    return f"https://{'www.' if kind != 'telegram' else ''}{host}{path}{query}"[:300]


def social_dict():
    saved = _settings("social.")
    return {k: saved.get(k, v) for k, v in SOCIAL_DEFAULTS.items()}


@app.get("/api/teacher")
def api_teacher():
    return jsonify({"teacher": teacher_dict(), "social": social_dict()})


@app.put("/api/admin/social")
@admin_required
def api_admin_social_update():
    data = body_json()
    names = {"instagram": "Instagram", "telegram": "Telegram", "youtube": "YouTube", "phone": "Telefon"}
    values = {}
    for k in SOCIAL_DEFAULTS:
        v = _social_url(k, data.get(k))
        if v is None:
            return err(f"{names[k]} havolasi noto‘g‘ri. Masalan: " + {
                "instagram": "https://www.instagram.com/username",
                "telegram": "https://t.me/username",
                "youtube": "https://www.youtube.com/@kanal",
                "phone": "+998 90 123 45 67"}[k])
        values[k] = v
    db = get_db()
    for k, v in values.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("social." + k, v))
    audit(g.user["id"], "social_update", "social", values)
    db.commit()
    return jsonify({"social": social_dict()})


@app.get("/teacher-photo")
def teacher_photo():
    photo = _settings("teacher.").get("photo")
    if photo and os.path.exists(os.path.join(DATA_DIR, photo)):
        return send_from_directory(DATA_DIR, photo, max_age=86400)
    if os.path.exists(DEFAULT_TEACHER_PHOTO):
        return send_from_directory(os.path.dirname(DEFAULT_TEACHER_PHOTO), os.path.basename(DEFAULT_TEACHER_PHOTO), max_age=86400)
    return err("Topilmadi.", 404)


@app.put("/api/admin/teacher")
@admin_required
def api_admin_teacher_update():
    data = body_json()
    db = get_db()
    values = {k: clean_text(data.get(k), lim) for k, lim in TEACHER_LIMITS.items()}
    if len(values["name"]) < 2:
        return err("O‘qituvchining ismini yozing.")
    values["show"] = "1" if data.get("show") else "0"
    for k, v in values.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("teacher." + k, v))
    audit(g.user["id"], "teacher_update", "teacher", values)
    db.commit()
    return jsonify({"teacher": teacher_dict()})


@app.post("/api/admin/teacher/photo")
@admin_required
def api_admin_teacher_photo():
    raw = request.get_data(cache=False)
    kind = next((v for sig, v in PHOTO_TYPES.items() if raw.startswith(sig)), None)
    if not raw or kind is None:
        return err("Rasm JPG yoki PNG formatida bo‘lsin.")
    db = get_db()
    old = _settings("teacher.").get("photo")
    name = f"teacher_photo.{kind[0]}"
    tmp = os.path.join(DATA_DIR, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, os.path.join(DATA_DIR, name))
    if old and old != name:
        try:
            os.remove(os.path.join(DATA_DIR, old))
        except OSError:
            pass
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('teacher.photo', ?)", (name,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('teacher.photo_v', ?)", (str(int(time.time())),))
    audit(g.user["id"], "teacher_photo", "teacher", {"bytes": len(raw)})
    db.commit()
    return jsonify({"teacher": teacher_dict()})


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# API: kurslar (ommaviy va o'quvchi uchun)
# ------------------------------------------------------------------
def course_dict(row):
    return {
        "id": row["id"], "title": row["title"], "description": row["description"], "price": row["price"],
        "order": row["sort_order"], "published": bool(row["published"]),
        "lessonCount": row["lesson_count"] if "lesson_count" in row.keys() else 0,
    }


def lesson_dict(row):
    return {
        "id": row["id"], "courseId": row["course_id"], "title": row["title"], "videoUrl": row["video_url"],
        "duration": row["duration"], "description": row["description"], "order": row["sort_order"],
        "hidden": bool(row["hidden"]),
    }


COURSE_SELECT = """
SELECT c.*, (SELECT COUNT(*) FROM lessons l WHERE l.course_id = c.id AND l.hidden = 0) AS lesson_count
FROM courses c
"""


@app.get("/api/courses")
def api_courses():
    rows = get_db().execute(COURSE_SELECT + " WHERE c.published = 1 ORDER BY c.sort_order, c.id").fetchall()
    return jsonify({"courses": [course_dict(r) for r in rows]})


@app.get("/api/my/courses")
@login_required
def api_my_courses():
    if g.user["status"] != "approved":
        return jsonify({"courses": []})
    db = get_db()
    rows = db.execute(COURSE_SELECT + """
        JOIN user_courses uc ON uc.course_id = c.id AND uc.user_id = ?
        ORDER BY c.sort_order, c.id""", (g.user["id"],)).fetchall()
    result = []
    for r in rows:
        c = course_dict(r)
        ls = db.execute("SELECT * FROM lessons WHERE course_id = ? AND hidden = 0 ORDER BY sort_order, id",
                        (r["id"],)).fetchall()
        c["lessons"] = [lesson_dict(x) for x in ls]
        result.append(c)
    return jsonify({"courses": result})


# ------------------------------------------------------------------
# API: admin
# ------------------------------------------------------------------
def _admin_users():
    db = get_db()
    rows = db.execute("""
        SELECT u.*,
               (SELECT COUNT(*) FROM logins lg WHERE lg.user_id = u.id) AS login_count
        FROM users u WHERE u.role != 'admin' ORDER BY u.created_at DESC, u.id DESC""").fetchall()
    grants = {}
    for gr in db.execute("SELECT user_id, course_id FROM user_courses").fetchall():
        grants.setdefault(gr["user_id"], []).append(gr["course_id"])
    users = []
    for r in rows:
        u = public_user(r)
        u.update(adminNote=r["admin_note"], loginCount=r["login_count"], courses=grants.get(r["id"], []),
                 statusChangedAt=r["status_changed_at"])
        users.append(u)
    return users


@app.get("/api/admin/users")
@admin_required
def api_admin_users():
    courses = get_db().execute(COURSE_SELECT + " ORDER BY c.sort_order, c.id").fetchall()
    return jsonify({"users": _admin_users(), "courses": [course_dict(c) for c in courses]})


@app.post("/api/admin/users/<int:uid>/status")
@admin_required
def api_admin_user_status(uid):
    data = body_json()
    status = data.get("status")
    if status not in STATUSES:
        return err("Noto‘g‘ri holat.")
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if target is None:
        return err("Foydalanuvchi topilmadi.", 404)
    if target["role"] == "admin":
        return err("Admin holatini bu yerdan o‘zgartirib bo‘lmaydi.")
    ts = now_iso()
    details = {"from": target["status"], "to": status}
    if "note" in data:
        db.execute("UPDATE users SET admin_note = ? WHERE id = ?", (clean_text(data.get("note"), 200), uid))
    if status == "approved" and "courses" in data:
        ids = {to_int(x, -1, -1, 10 ** 9) for x in (data.get("courses") or [])}
        valid = {r["id"] for r in db.execute("SELECT id FROM courses").fetchall()}
        ids &= valid
        if not ids:
            return err("Kamida bitta kursni belgilang.")
        current = {r["course_id"] for r in db.execute("SELECT course_id FROM user_courses WHERE user_id = ?", (uid,))}
        for cid in ids - current:
            db.execute("INSERT INTO user_courses (user_id, course_id, granted_at) VALUES (?,?,?)", (uid, cid, ts))
        for cid in current - ids:
            db.execute("DELETE FROM user_courses WHERE user_id = ? AND course_id = ?", (uid, cid))
        details["courses"] = sorted(ids)
    db.execute("UPDATE users SET status = ?, status_changed_at = ? WHERE id = ?", (status, ts, uid))
    audit(g.user["id"], "user_status", uid, details)
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/admin/users.csv")
@admin_required
def api_admin_users_csv():
    db = get_db()
    titles = {r["id"]: r["title"] for r in db.execute("SELECT id, title FROM courses")}
    labels = {"pending": "Kutilmoqda", "approved": "Ruxsat berilgan", "rejected": "Rad etilgan", "blocked": "Bloklangan"}

    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") else s

    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["ID", "Ism", "Email", "Telefon", "Hudud", "Ro‘yxatdan o‘tgan", "Oxirgi kirish", "Kirishlar soni", "Holat", "Kurslar", "Izoh"])
    for u in _admin_users():
        w.writerow([u["id"], safe(u["name"]), safe(u["email"]), (u["phone"] or "").lstrip("+"), safe(u["region"]), u["createdAt"], u["lastLoginAt"] or "",
                    u["loginCount"], labels.get(u["status"], u["status"]),
                    safe("; ".join(titles.get(c, "") for c in u["courses"])), safe(u["adminNote"])])
    fname = f"kompsavod-oquvchilar-{datetime.now():%Y-%m-%d}.csv"
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


def _course_payload(data):
    title = clean_text(data.get("title"), 120)
    if len(title) < 2:
        return None, "Kurs nomini yozing."
    return {
        "title": title,
        "description": clean_text(data.get("description"), 600),
        "price": clean_text(data.get("price"), 60),
        "sort_order": to_int(data.get("order"), 0),
        "published": 1 if data.get("published") else 0,
    }, None


@app.get("/api/admin/courses")
@admin_required
def api_admin_courses():
    rows = get_db().execute(COURSE_SELECT + " ORDER BY c.sort_order, c.id").fetchall()
    return jsonify({"courses": [course_dict(r) for r in rows]})


@app.post("/api/admin/courses")
@admin_required
def api_admin_course_create():
    p, e = _course_payload(body_json())
    if e:
        return err(e)
    db = get_db()
    ts = now_iso()
    cur = db.execute("INSERT INTO courses (title, description, price, sort_order, published, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (p["title"], p["description"], p["price"], p["sort_order"], p["published"], ts, ts))
    audit(g.user["id"], "course_create", cur.lastrowid, p)
    db.commit()
    return jsonify({"id": cur.lastrowid})


@app.put("/api/admin/courses/<int:cid>")
@admin_required
def api_admin_course_update(cid):
    p, e = _course_payload(body_json())
    if e:
        return err(e)
    db = get_db()
    if db.execute("SELECT 1 FROM courses WHERE id = ?", (cid,)).fetchone() is None:
        return err("Kurs topilmadi.", 404)
    db.execute("UPDATE courses SET title=?, description=?, price=?, sort_order=?, published=?, updated_at=? WHERE id=?",
               (p["title"], p["description"], p["price"], p["sort_order"], p["published"], now_iso(), cid))
    audit(g.user["id"], "course_update", cid, p)
    db.commit()
    return jsonify({"ok": True})


def _lesson_payload(data):
    title = clean_text(data.get("title"), 160)
    if len(title) < 2:
        return None, "Dars nomini yozing."
    url = clean_text(data.get("videoUrl"), 500)
    if url and not VIDEO_RE.match(url):
        return None, "Faqat YouTube yoki Vimeo havolasi qabul qilinadi."
    return {
        "title": title, "video_url": url,
        "duration": clean_text(data.get("duration"), 20),
        "description": clean_text(data.get("description"), 2000),
        "sort_order": to_int(data.get("order"), 0, 0),
        "hidden": 1 if data.get("hidden") else 0,
    }, None


@app.get("/api/admin/courses/<int:cid>/lessons")
@admin_required
def api_admin_lessons(cid):
    rows = get_db().execute("SELECT * FROM lessons WHERE course_id = ? ORDER BY sort_order, id", (cid,)).fetchall()
    return jsonify({"lessons": [lesson_dict(r) for r in rows]})


@app.post("/api/admin/courses/<int:cid>/lessons")
@admin_required
def api_admin_lesson_create(cid):
    p, e = _lesson_payload(body_json())
    if e:
        return err(e)
    db = get_db()
    if db.execute("SELECT 1 FROM courses WHERE id = ?", (cid,)).fetchone() is None:
        return err("Kurs topilmadi.", 404)
    if not p["sort_order"]:
        p["sort_order"] = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM lessons WHERE course_id = ?",
                                     (cid,)).fetchone()[0]
    ts = now_iso()
    cur = db.execute("INSERT INTO lessons (course_id, title, video_url, duration, description, sort_order, hidden, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, p["title"], p["video_url"], p["duration"], p["description"], p["sort_order"], p["hidden"], ts, ts))
    audit(g.user["id"], "lesson_create", cur.lastrowid, {"course": cid, "title": p["title"]})
    db.commit()
    return jsonify({"id": cur.lastrowid})


@app.put("/api/admin/lessons/<int:lid>")
@admin_required
def api_admin_lesson_update(lid):
    p, e = _lesson_payload(body_json())
    if e:
        return err(e)
    db = get_db()
    row = db.execute("SELECT * FROM lessons WHERE id = ?", (lid,)).fetchone()
    if row is None:
        return err("Dars topilmadi.", 404)
    if not p["sort_order"]:
        p["sort_order"] = row["sort_order"]
    db.execute("UPDATE lessons SET title=?, video_url=?, duration=?, description=?, sort_order=?, hidden=?, updated_at=? "
               "WHERE id=?",
               (p["title"], p["video_url"], p["duration"], p["description"], p["sort_order"], p["hidden"], now_iso(), lid))
    audit(g.user["id"], "lesson_update", lid, {"title": p["title"], "hidden": p["hidden"]})
    db.commit()
    return jsonify({"ok": True})


@app.errorhandler(404)
def _not_found(e):
    if request.path.startswith("/api/"):
        return err("Topilmadi.", 404)
    return index()


@app.errorhandler(500)
def _server_error(e):
    log.exception("Server xatosi")
    return err("Serverda xatolik yuz berdi.", 500)


# ------------------------------------------------------------------
# Frontend (React)
# ------------------------------------------------------------------
@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/admin")
@app.get("/admin/")
def admin_entry():
    return index()


INDEX_HTML = r"""<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KompSavod – kompyuter savodxonligi kurslari</title>
<meta name="description" content="Kompyuterni noldan o‘rganing: Windows, Internet, Word, Excel va PowerPoint bo‘yicha o‘zbek tilidagi video darslar.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 44 44'%3E%3Crect x='2' y='6' width='40' height='27' rx='7' fill='%233A44D6'/%3E%3Cpath d='M13 20l6 6 12-12' stroke='white' stroke-width='4' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
/* KompSavod – umumiy uslublar */
:root {
  --indigo-900: #1A1E8C;
  --indigo-700: #262BA8;
  --indigo-600: #3A44D6;
  --indigo-500: #4B57EE;
  --mint: #3DDBB5;
  --mint-dark: #0E6B55;
  --mint-soft: #E3F9F2;
  --ink: #14183F;
  --ink-on-mint: #0E1250;
  --muted: #4A5078;
  --line: #E3E5F5;
  --soft: #EEF0FF;
  --paper: #F5F6FF;
  --white: #FFFFFF;
  --danger: #B42318;
  --danger-soft: #FDECEA;
  --warn: #8A5A00;
  --warn-soft: #FFF4D6;
  --on-dark-muted: #E4E7FF;
  --radius-lg: 24px;
  --radius: 14px;
  --font: "Manrope", "Segoe UI", Arial, sans-serif;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; font-family: var(--font); color: var(--ink); background: var(--paper); line-height: 1.5; }
img { max-width: 100%; }
a { color: inherit; }
:focus-visible { outline: 3px solid var(--mint); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } * { transition: none !important; animation: none !important; } }

.wrap { max-width: 1240px; margin: 0 auto; padding: 0 24px; }
.hidden { display: none !important; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* ---------- Tugmalar ---------- */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 10px; min-height: 48px; padding: 12px 24px; border-radius: var(--radius); border: 0; font: inherit; font-weight: 800; font-size: 16px; cursor: pointer; text-decoration: none; transition: transform .12s ease, opacity .12s ease; }
.btn:active { transform: translateY(1px); }
.btn[disabled] { opacity: .55; cursor: not-allowed; }
.btn-mint { background: var(--mint); color: var(--ink-on-mint); }
.btn-indigo { background: var(--indigo-600); color: var(--white); }
.btn-ghost { background: rgba(255,255,255,0.12); color: var(--white); border: 1px solid rgba(255,255,255,0.3); }
.btn-light { background: var(--white); color: var(--ink); border: 1px solid var(--line); }
.btn-danger { background: var(--danger-soft); color: var(--danger); }
.btn-sm { min-height: 38px; padding: 8px 14px; font-size: 14px; border-radius: 10px; }
.btn-lg { min-height: 58px; padding: 16px 30px; font-size: 18px; }

/* ---------- Qorong'i (gradient) fon ---------- */
.dark-bg {
  color: var(--white);
  background-color: var(--indigo-700);
  background-image:
    linear-gradient(rgba(255,255,255,0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.07) 1px, transparent 1px),
    linear-gradient(180deg, var(--indigo-500) 0%, var(--indigo-600) 35%, var(--indigo-700) 75%, var(--indigo-900) 100%);
  background-size: 120px 120px, 120px 120px, 100% 100%;
}
.glass { background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.24); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); }

/* ---------- Navigatsiya ---------- */
.nav { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 0; }
.brand { display: inline-flex; align-items: center; gap: 12px; text-decoration: none; color: var(--white); font-size: 24px; font-weight: 800; letter-spacing: -0.5px; }
.nav-links { display: flex; flex-wrap: wrap; align-items: center; gap: 24px; font-weight: 600; }
.nav-links a:not(.btn) { color: var(--white); text-decoration: none; }
.nav-links a:not(.btn):hover { color: var(--on-dark-muted); text-decoration: underline; }

/* ---------- Bosh sahifa ---------- */
.hero { text-align: center; padding: 48px 0 0; display: flex; flex-direction: column; align-items: center; gap: 24px; }
.pill { display: inline-flex; align-items: center; gap: 10px; padding: 10px 18px; border-radius: 999px; font-weight: 600; font-size: 15px; }
.pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--mint); }
.hero h1 { margin: 0; font-size: clamp(38px, 6vw, 76px); line-height: 1.05; letter-spacing: -2px; font-weight: 800; max-width: 980px; }
.hero p { margin: 0; font-size: clamp(17px, 2vw, 21px); color: var(--on-dark-muted); max-width: 720px; }
.hero-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 14px; margin-top: 8px; }

.laptop { width: 100%; max-width: 980px; margin-top: 48px; position: relative; text-align: left; }
.laptop-screen { background: #0F1236; border-radius: 22px 22px 0 0; padding: 14px; box-shadow: 0 40px 80px rgba(8,10,60,0.45); }
.laptop-inner { background: var(--paper); color: var(--ink); border-radius: 10px; overflow: hidden; display: flex; flex-wrap: wrap; }
.laptop-main { flex: 999 1 480px; min-width: 0; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
.laptop-side { flex: 1 1 240px; background: var(--white); border-left: 1px solid var(--line); padding: 20px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; color: var(--muted); }
.laptop-base { height: 18px; background: linear-gradient(180deg, #C9CCE8, #9CA0C9); border-radius: 0 0 26px 26px; margin: 0 -24px; }
.sheet { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); font-size: 13px; background: var(--white); border: 1px solid #D9DCF2; border-radius: 10px; overflow: hidden; }
.sheet div { padding: 9px; border-bottom: 1px solid #ECEEF8; }
.sheet .h { background: var(--soft); font-weight: 700; }
.sheet .f { background: var(--mint-soft); color: var(--mint-dark); font-weight: 700; outline: 2px solid #1FA383; outline-offset: -2px; }
.side-item { display: flex; align-items: center; gap: 10px; }
.side-item .c { width: 20px; height: 20px; border-radius: 50%; flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; }
.side-item .done { background: #1FA383; color: var(--white); font-size: 12px; font-weight: 800; }
.side-item .todo { border: 2px solid #C5C9E6; }
.side-item.now { background: var(--soft); color: var(--indigo-600); font-weight: 700; padding: 8px; border-radius: 8px; }
.side-item.now .c { border: 2px solid var(--indigo-600); }

.float-card { position: absolute; z-index: 2; background: var(--white); color: var(--ink); border-radius: 16px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; box-shadow: 0 20px 40px rgba(8,10,60,0.35); font-weight: 700; font-size: 15px; }
.float-card small { display: block; font-size: 13px; font-weight: 600; color: var(--muted); }
.float-card .ic { width: 40px; height: 40px; border-radius: 12px; display: inline-flex; align-items: center; justify-content: center; }
@media (max-width: 1180px) { .float-card { display: none; } }

.section { padding-top: 110px; }
.section-head { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 12px; margin-bottom: 40px; }
.section-head h2 { margin: 0; font-size: clamp(30px, 4vw, 48px); letter-spacing: -1px; font-weight: 800; }
.section-head p { margin: 0; font-size: 19px; color: var(--on-dark-muted); max-width: 660px; }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; }
.card-glass { border-radius: 20px; padding: 28px; display: flex; flex-direction: column; gap: 12px; }
.card-glass .meta { font-size: 14px; font-weight: 700; color: var(--mint); }
.card-glass h3 { margin: 0; font-size: 22px; font-weight: 800; }
.card-glass p { margin: 0; color: var(--on-dark-muted); }
.teacher { display: grid; grid-template-columns: minmax(220px, 340px) 1fr; gap: 40px; align-items: center; border-radius: 24px; padding: 32px; }
.teacher img { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; border-radius: 20px; display: block; }
.teacher .t-body { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.teacher h3 { margin: 0; font-size: clamp(26px, 3vw, 36px); font-weight: 800; letter-spacing: -0.5px; }
.teacher .t-role { color: var(--mint); font-weight: 700; font-size: 18px; }
.teacher p { margin: 0; color: var(--on-dark-muted); font-size: 18px; white-space: pre-line; }
.teacher-stats { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; }
.teacher-stats > div { border-radius: 16px; padding: 14px 20px; min-width: 140px; }
.teacher-stats b { display: block; font-size: 30px; font-weight: 800; color: var(--mint); line-height: 1.1; }
.teacher-stats span { color: var(--on-dark-muted); font-size: 15px; }
.socials { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; }
.socials a { display: inline-flex; align-items: center; gap: 8px; padding: 10px 16px; border-radius: 14px; font-weight: 700; text-decoration: none; color: inherit; }
.socials a:hover, .footer-links a:hover { background: rgba(255,255,255,0.18); }
.socials svg, .footer-links svg { flex: none; }
.footer-links a { display: inline-flex; align-items: center; gap: 8px; color: inherit; }
.teacher-photo-admin { width: 160px; height: 160px; object-fit: cover; border-radius: 16px; display: block; border: 1px solid var(--line); }
@media (max-width: 760px) { .teacher { grid-template-columns: 1fr; padding: 20px; gap: 22px; } .teacher img { max-width: 320px; margin: 0 auto; } }
.card-white { border-radius: 20px; padding: 28px; display: flex; flex-direction: column; gap: 14px; background: var(--white); color: var(--ink); }
.card-white h3 { margin: 0; font-size: 20px; font-weight: 800; }
.card-white p { margin: 0; color: var(--muted); }
.icon-box { width: 52px; height: 52px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; }

.course-card .price { font-size: 26px; font-weight: 800; color: var(--white); }
.course-card .actions { margin-top: auto; padding-top: 8px; }
.empty-dark { text-align: center; padding: 40px; border-radius: 20px; color: var(--on-dark-muted); }

.steps .num { width: 48px; height: 48px; border-radius: 14px; background: var(--mint); color: var(--ink-on-mint); display: flex; align-items: center; justify-content: center; font-size: 22px; font-weight: 800; }

.faq { max-width: 900px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
.faq details { border-radius: 18px; padding: 20px 24px; }
.faq summary { cursor: pointer; list-style: none; font-size: 18px; font-weight: 800; display: flex; justify-content: space-between; gap: 16px; align-items: center; }
.faq summary::-webkit-details-marker { display: none; }
.faq summary::after { content: "+"; font-size: 26px; color: var(--mint); }
.faq details[open] summary::after { content: "–"; }
.faq p { margin: 12px 0 0; color: var(--on-dark-muted); font-size: 17px; }

.cta-band { border-radius: 28px; padding: 44px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 28px; }
.cta-band h2 { margin: 0 0 8px; font-size: clamp(26px, 3.2vw, 38px); }
.cta-band p { margin: 0; color: var(--on-dark-muted); font-size: 18px; }

.footer { padding: 80px 0 40px; text-align: center; }
.footer-links { display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; margin-bottom: 24px; }
.footer-links span, .footer-links a { padding: 12px 18px; border-radius: 14px; font-weight: 600; text-decoration: none; }
.footer small { color: #C9CDF5; }

/* ---------- Ilova sahifalari (kabinet, admin) ---------- */
.app-header { background: var(--indigo-600); color: var(--white); }
.app-header .nav { padding: 14px 0; }
.app-header .user-chip { font-weight: 700; color: var(--on-dark-muted); }
.page { padding: 32px 0 64px; }
.page h1 { margin: 0 0 6px; font-size: clamp(26px, 3vw, 34px); letter-spacing: -0.5px; }
.lead { margin: 0 0 24px; color: var(--muted); }
.panel { background: var(--white); border: 1px solid var(--line); border-radius: var(--radius-lg); padding: 24px; }
.panel + .panel { margin-top: 20px; }
.panel h2 { margin: 0 0 16px; font-size: 20px; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 16px; margin-bottom: 20px; }
.stat { background: var(--white); border: 1px solid var(--line); border-radius: 18px; padding: 18px 20px; }
.stat .n { font-size: 32px; font-weight: 800; }
.stat .l { color: var(--muted); font-size: 14px; font-weight: 600; }

.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.tab { min-height: 44px; padding: 10px 18px; border-radius: 12px; border: 1px solid var(--line); background: var(--white); font: inherit; font-weight: 700; cursor: pointer; color: var(--ink); }
.tab[aria-selected="true"] { background: var(--ink); color: var(--white); border-color: var(--ink); }

.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }
.toolbar .grow { flex: 1 1 240px; }

.table-box { overflow-x: auto; border: 1px solid var(--line); border-radius: 16px; background: var(--white); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 12px 14px; border-bottom: 1px solid var(--line); vertical-align: middle; white-space: nowrap; }
th { background: var(--soft); font-weight: 800; color: var(--ink); position: sticky; top: 0; }
tr:last-child td { border-bottom: 0; }
td .row-actions { display: flex; gap: 6px; flex-wrap: wrap; }

.badge { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; }
.badge.pending { background: var(--warn-soft); color: var(--warn); }
.badge.approved { background: var(--mint-soft); color: var(--mint-dark); }
.badge.rejected, .badge.blocked { background: var(--danger-soft); color: var(--danger); }
.badge.draft { background: var(--soft); color: var(--muted); }

/* ---------- Formalar ---------- */
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
.field label { font-weight: 700; font-size: 15px; }
.field .hint { font-size: 13px; color: var(--muted); }
.input, select.input, textarea.input { width: 100%; min-height: 48px; padding: 12px 14px; border-radius: 12px; border: 1.5px solid #CDD1EA; font: inherit; font-size: 16px; color: var(--ink); background: var(--white); }
textarea.input { min-height: 110px; resize: vertical; }
.input:focus { border-color: var(--indigo-600); outline: none; box-shadow: 0 0 0 3px rgba(58,68,214,0.18); }
.phone-row { display: flex; align-items: stretch; border: 1.5px solid #CDD1EA; border-radius: 12px; overflow: hidden; background: var(--white); }
.phone-row:focus-within { border-color: var(--indigo-600); box-shadow: 0 0 0 3px rgba(58,68,214,0.18); }
.phone-row .prefix { display: flex; align-items: center; padding: 0 14px; background: var(--soft); font-weight: 800; }
.phone-row input { border: 0; outline: none; flex: 1; min-height: 52px; padding: 0 14px; font: inherit; font-size: 20px; font-weight: 700; letter-spacing: 1px; min-width: 0; }
.code-input { font-size: 28px !important; letter-spacing: 10px; text-align: center; font-weight: 800; }
.check { display: flex; align-items: center; gap: 10px; padding: 8px 0; font-weight: 600; }
.check input { width: 20px; height: 20px; }
.form-error { background: var(--danger-soft); color: var(--danger); padding: 12px 14px; border-radius: 12px; font-weight: 600; margin-bottom: 16px; }
.form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 16px; }

/* ---------- Kirish sahifasi ---------- */
.auth-page { min-height: 100vh; display: flex; flex-direction: column; }
.auth-center { flex: 1; display: flex; align-items: center; justify-content: center; padding: 24px; }
.auth-card { width: 100%; max-width: 460px; background: var(--white); color: var(--ink); border-radius: 28px; padding: 36px; box-shadow: 0 30px 60px rgba(8,10,60,0.35); }
.auth-card h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: -0.5px; }
.auth-card .lead { margin-bottom: 24px; }
.link-btn { background: none; border: 0; padding: 0; font: inherit; color: var(--indigo-600); font-weight: 700; cursor: pointer; text-decoration: underline; }
.link-btn[disabled] { color: var(--muted); text-decoration: none; cursor: default; }

/* ---------- Kabinet ---------- */
.status-card { border-radius: var(--radius-lg); padding: 32px; display: flex; flex-direction: column; gap: 10px; }
.status-card.pending { background: var(--warn-soft); }
.status-card.rejected, .status-card.blocked { background: var(--danger-soft); }
.status-card h2 { margin: 0; font-size: 24px; }
.status-card p { margin: 0; color: var(--ink); }
.player-layout { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
.player-main { flex: 999 1 560px; min-width: 0; }
.player-side { flex: 1 1 280px; }
.video-box { position: relative; aspect-ratio: 16 / 9; background: #0F1236; border-radius: 18px; overflow: hidden; }
.video-box iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
.video-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--on-dark-muted); padding: 24px; text-align: center; }
.lesson-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.lesson-list button { width: 100%; text-align: left; display: flex; gap: 12px; align-items: center; padding: 12px; border-radius: 12px; border: 1px solid transparent; background: none; font: inherit; color: var(--ink); cursor: pointer; }
.lesson-list button:hover { background: var(--soft); }
.lesson-list button[aria-current="true"] { background: var(--soft); border-color: var(--indigo-600); color: var(--indigo-600); font-weight: 700; }
.lesson-list .no { width: 28px; height: 28px; border-radius: 8px; background: var(--soft); display: inline-flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 800; flex: 0 0 auto; }
.course-pick { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }

/* ---------- Dialog ---------- */
dialog { border: 0; border-radius: 24px; padding: 0; width: min(560px, calc(100vw - 32px)); color: var(--ink); }
dialog::backdrop { background: rgba(14,18,80,0.55); }
.dialog-body { padding: 28px; }
.dialog-body h2 { margin: 0 0 6px; font-size: 22px; }
.dialog-actions { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; margin-top: 20px; }

/* ---------- Xabarlar ---------- */
.toast { position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 140%); background: var(--ink); color: var(--white); padding: 14px 20px; border-radius: 14px; font-weight: 700; z-index: 1000; max-width: calc(100vw - 32px); transition: transform .25s ease; }
.toast.show { transform: translate(-50%, 0); }
.toast.error { background: var(--danger); }
.toast.success { background: var(--mint-dark); }
.config-warning { background: var(--warn-soft); color: var(--warn); padding: 12px 20px; text-align: center; font-weight: 700; }
.loading { padding: 40px; text-align: center; color: var(--muted); }

@media (max-width: 640px) {
  .nav-links { gap: 14px; font-size: 15px; }
  .auth-card { padding: 26px; }
  .cta-band { padding: 28px; }
}

/* ---------- Modal (bitta faylli versiya) ---------- */
.modal-overlay { position: fixed; inset: 0; background: rgba(14,18,80,0.55); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 900; }
.modal { background: var(--white); color: var(--ink); border-radius: 24px; width: min(560px, 100%); max-height: calc(100vh - 32px); overflow: auto; }
.dev-note { background: var(--warn-soft); color: var(--warn); padding: 10px 14px; border-radius: 12px; font-weight: 600; font-size: 14px; margin-bottom: 16px; }
.nav-links button.navlink { background: none; border: 0; color: var(--white); font: inherit; font-weight: 600; cursor: pointer; padding: 0; }
.nav-links button.navlink:hover { text-decoration: underline; }

</style>
</head>
<body>
<div id="root"><div class="loading">Yuklanmoqda…</div></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" crossorigin></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" crossorigin></script>
<script src="https://unpkg.com/@babel/standalone@7.24.7/babel.min.js" crossorigin></script>
<script type="text/babel" data-presets="react">
const { useState, useEffect, useCallback } = React;

/* ================= Yordamchilar ================= */
async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "kompsavod" },
    body: body ? JSON.stringify(body) : undefined
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* bo'sh javob */ }
  if (!res.ok) throw new Error(data.error || "Xatolik yuz berdi. Qayta urinib ko‘ring.");
  return data;
}

function fmtPhone(p) {
  const m = String(p || "").match(/^\+998(\d{2})(\d{3})(\d{2})(\d{2})$/);
  return m ? `+998 ${m[1]} ${m[2]} ${m[3]} ${m[4]}` : String(p || "");
}

function fmtDate(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
  return withTime ? `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}` : date;
}

function videoEmbed(url) {
  const s = String(url || "").trim();
  let m = s.match(/^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})/);
  if (m) return `https://www.youtube-nocookie.com/embed/${m[1]}?rel=0&modestbranding=1`;
  m = s.match(/^https?:\/\/(?:www\.|player\.)?vimeo\.com\/(?:video\/)?(\d+)(?:\/(\w+))?/);
  if (m) return `https://player.vimeo.com/video/${m[1]}${m[2] ? `?h=${m[2]}` : ""}`;
  return null;
}

const STATUS_LABELS = { pending: "Kutilmoqda", approved: "Ruxsat berilgan", rejected: "Rad etilgan", blocked: "Bloklangan" };
const REGIONS = ["Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Farg‘ona", "Jizzax", "Xorazm", "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog‘iston", "Samarqand", "Sirdaryo", "Surxondaryo", "Chet elda"];

const onAdminPath = () => location.pathname.replace(/\/+$/, "") === "/admin";
// Admin panel doim /admin manzilida turadi
const goAdmin = () => {
  if (!onAdminPath()) { location.href = "/admin"; return; }
  if (location.hash) history.replaceState(null, "", "/admin");
};
const routeOf = () => onAdminPath() ? "#/admin" : (location.hash || "#/");
if (!onAdminPath() && (location.hash || "").startsWith("#/admin")) location.replace("/admin");

function useHash() {
  const [hash, setHash] = useState(routeOf);
  useEffect(() => {
    const f = () => {
      if (onAdminPath() && location.hash && location.hash !== "#/admin") { location.href = "/" + location.hash; return; }
      if (!onAdminPath() && (location.hash || "").startsWith("#/admin")) { location.href = "/admin"; return; }
      setHash(routeOf()); window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return hash;
}
const go = (h) => { location.hash = h; };

function Logo() {
  return (
    <svg width="36" height="36" viewBox="0 0 44 44" fill="none" aria-hidden="true">
      <rect x="2" y="6" width="40" height="27" rx="7" fill="currentColor"></rect>
      <rect x="16" y="35" width="12" height="4" rx="2" fill="currentColor" opacity="0.7"></rect>
      <path d="M13 20l6 6 12-12" stroke="#3A44D6" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"></path>
    </svg>
  );
}

function Toast({ toast }) {
  return (
    <div role="status" aria-live="polite" className={`toast ${toast ? "show " + toast.kind : ""}`}>
      {toast ? toast.text : ""}
    </div>
  );
}

function Modal({ title, children, onClose }) {
  useEffect(() => {
    const f = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
  return (
    <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="dialog-body">
          <h2>{title}</h2>
          {children}
        </div>
      </div>
    </div>
  );
}

const SOCIALS = [
  ["instagram", "Instagram", <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5" /><circle cx="12" cy="12" r="4" /><circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" /></svg>],
  ["telegram", "Telegram", <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 4L3 11l6 2.5M21 4l-3 16-9-6.5M21 4L9 13.5V19l3-3.5" /></svg>],
  ["youtube", "YouTube", <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="4" /><path d="M10 9l5 3-5 3z" fill="currentColor" /></svg>],
];
const socialHandle = (url) => {
  const m = String(url || "").match(/^https?:\/\/[^/]+\/([^/?#]+)/);
  return m ? (m[1].startsWith("@") ? m[1] : "@" + m[1]) : "";
};
function SocialLinks({ social, className, withHandle }) {
  const items = SOCIALS.filter(([k]) => social && social[k]);
  if (!items.length) return null;
  return (
    <div className={className}>
      {items.map(([k, label, icon]) => (
        <a key={k} className="glass" href={social[k]} target="_blank" rel="noopener noreferrer">
          {icon}{withHandle ? `${label}: ${socialHandle(social[k])}` : label}
        </a>
      ))}
    </div>
  );
}

function TgContact() {
  const [url, setUrl] = useState(null);
  useEffect(() => { api("/api/teacher").then(d => setUrl(d.social.telegram || null)).catch(() => {}); }, []);
  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{socialHandle(url)}</a> : <>administrator</>;
}

/* ================= Ilova ================= */
function App() {
  const hash = useHash();
  const [me, setMe] = useState(undefined);
  const [devMode, setDevMode] = useState(false);
  const [toast, setToast] = useState(null);

  const notify = useCallback((text, kind = "info") => {
    setToast({ text, kind });
    clearTimeout(window.__toastT);
    window.__toastT = setTimeout(() => setToast(null), 3800);
  }, []);

  const reloadMe = useCallback(async () => {
    try {
      const d = await api("/api/me");
      setMe(d.user);
      setDevMode(!!d.devMode);
    } catch (e) { setMe(null); }
  }, []);

  useEffect(() => { reloadMe(); }, [reloadMe]);

  const logout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    setMe(null);
    go("#/");
  };

  const onLogin = (user) => {
    setMe(user);
    if (user.role === "admin") goAdmin(); else go("#/kabinet");
  };

  let page;
  if (me === undefined) page = <div className="loading">Yuklanmoqda…</div>;
  else if (hash.startsWith("#/kirish") || hash.startsWith("#/royxat"))
    page = <Login me={me} onLogin={onLogin} mode={hash.startsWith("#/royxat") ? "signup" : "login"} />;
  else if (hash.startsWith("#/kabinet")) page = <Cabinet me={me} logout={logout} />;
  else if (hash.startsWith("#/admin")) page = <Admin me={me} logout={logout} notify={notify} onLogin={onLogin} />;
  else page = <Landing me={me} notify={notify} />;

  return <>{page}<Toast toast={toast} /></>;
}

/* ================= Bosh sahifa ================= */
function Landing({ me }) {
  const [courses, setCourses] = useState(null);
  const [teacher, setTeacher] = useState(null);
  const [social, setSocial] = useState(null);
  useEffect(() => {
    document.body.className = "dark-bg";
    api("/api/courses").then(d => setCourses(d.courses)).catch(() => setCourses([]));
    api("/api/teacher").then(d => { setTeacher(d.teacher); setSocial(d.social); }).catch(() => setTeacher(null));
    return () => { document.body.className = ""; };
  }, []);
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  // Admin bosh sahifada oddiy mehmon kabi ko'radi (admin panel faqat /admin orqali)
  const user = me && me.role !== "admin" ? me : null;
  const authHref = user ? "#/kabinet" : "#/kirish";
  const authText = user ? "Kabinet" : "Kirish";
  const signupHref = user ? "#/kabinet" : "#/royxat";

  return (
    <div className="wrap">
      <nav className="nav" aria-label="Asosiy menyu">
        <a className="brand" href="#/"><Logo />KompSavod</a>
        <div className="nav-links">
          <button className="navlink" onClick={() => scrollTo("kurslar")}>Kurslar</button>
          {teacher?.show && <button className="navlink" onClick={() => scrollTo("oqituvchi")}>O‘qituvchi</button>}
          <button className="navlink" onClick={() => scrollTo("qanday")}>Qanday ishlaydi</button>
          <button className="navlink" onClick={() => scrollTo("savollar")}>Savollar</button>
          <button className="navlink" onClick={() => scrollTo("aloqa")}>Aloqa</button>
          <a className="btn btn-mint btn-sm" href={authHref}>{authText}</a>
        </div>
      </nav>

      <header className="hero">
        <div className="pill glass"><span className="dot"></span>O‘zbek tilidagi kompyuter savodxonligi kurslari</div>
        <h1>Kompyuterni noldan o‘rganing – oson va tushunarli</h1>
        <p>Windows, Internet, Word, Excel va PowerPoint bo‘yicha qisqa video darslar. Har darsdan keyin amaliy topshiriq va savollaringizga javob.</p>
        <div className="hero-actions">
          <a className="btn btn-mint btn-lg" href={signupHref}>{user ? "Kabinetga o‘tish" : "Ro‘yxatdan o‘tish"}</a>
          <button className="btn btn-ghost btn-lg" onClick={() => scrollTo("kurslar")}>Kurslarni ko‘rish</button>
        </div>
        <div className="laptop" aria-hidden="true">
          <div className="float-card" style={{ left: -150, top: 90 }}><span className="ic" style={{ background: "#E3F9F2" }}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#1FA383" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l5 5L20 7" /></svg></span><span>Amaliy topshiriq<small>har darsdan keyin</small></span></div>
          <div className="float-card" style={{ right: -150, top: 230 }}><span className="ic" style={{ background: "#EEF0FF" }}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#3A44D6" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="9" r="6" /><path d="M8.5 14L7 22l5-3 5 3-1.5-8" /></svg></span><span>Sertifikat<small>kurs oxirida</small></span></div>
          <div className="laptop-screen">
            <div className="laptop-inner">
              <div className="laptop-main">
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}><b style={{ color: "#3A44D6" }}>Microsoft Excel</b><span style={{ color: "#4A5078" }}>11:42</span></div>
                <div style={{ fontSize: 21, fontWeight: 800 }}>SUM, AVERAGE, MIN, MAX funksiyalari</div>
                <div className="sheet">
                  <div className="h">Oy</div><div className="h">Oziq-ovqat</div><div className="h">Transport</div><div className="h">Jami</div>
                  <div>Yanvar</div><div>1 200 000</div><div>300 000</div><div><b>1 500 000</b></div>
                  <div>Fevral</div><div>1 100 000</div><div>280 000</div><div><b>1 380 000</b></div>
                  <div>Mart</div><div>1 250 000</div><div>310 000</div><div className="f">=SUM(B4:C4)</div>
                </div>
              </div>
              <div className="laptop-side">
                <b style={{ color: "#14183F", fontSize: 14 }}>Darslar</b>
                <div className="side-item"><span className="c done">✓</span>Excel bilan tanishuv</div>
                <div className="side-item"><span className="c done">✓</span>Ma’lumot kiritish</div>
                <div className="side-item now"><span className="c"></span>SUM, AVERAGE…</div>
                <div className="side-item"><span className="c todo"></span>Saralash va filtrlash</div>
              </div>
            </div>
          </div>
          <div className="laptop-base"></div>
        </div>
      </header>

      <section className="section" id="kurslar">
        <div className="section-head"><h2>Kurslar</h2><p>Ro‘yxatdan o‘ting, admin tasdiqlagach darslar kabinetingizda ochiladi.</p></div>
        <div className="grid">
          {courses === null && <div className="empty-dark glass">Kurslar yuklanmoqda…</div>}
          {courses && courses.length === 0 && <div className="empty-dark glass">Kurslar tez orada qo‘shiladi.</div>}
          {courses && courses.map(c => (
            <article key={c.id} className="card-glass glass course-card">
              <div className="meta">{c.lessonCount} ta dars</div>
              <h3>{c.title}</h3>
              <p style={{ whiteSpace: "pre-line" }}>{c.description}</p>
              {c.price && <div className="price">{c.price}</div>}
              <div className="actions"><a className="btn btn-mint" href={signupHref}>Kursga yozilish</a></div>
            </article>
          ))}
        </div>
      </section>

      {teacher?.show && (
        <section className="section" id="oqituvchi">
          <div className="section-head"><h2>O‘qituvchingiz bilan tanishing</h2></div>
          <div className="teacher glass">
            {teacher.photoUrl && <img src={teacher.photoUrl} alt={teacher.name} loading="lazy" />}
            <div className="t-body">
              <h3>{teacher.name}</h3>
              {teacher.title && <div className="t-role">{teacher.title}</div>}
              {teacher.bio && <p>{teacher.bio}</p>}
              {(teacher.years || teacher.students) && (
                <div className="teacher-stats">
                  {teacher.years && <div className="glass"><b>{teacher.years}</b><span>yillik tajriba</span></div>}
                  {teacher.students && <div className="glass"><b>{teacher.students}</b><span>o‘quvchi</span></div>}
                </div>
              )}
              <SocialLinks social={social} className="socials" />
            </div>
          </div>
        </section>
      )}

      <section className="section" id="qanday">
        <div className="section-head"><h2>Qanday ishlaydi</h2></div>
        <div className="grid steps">
          {[["Ro‘yxatdan o‘ting", "Ism, email va parol bilan bir daqiqada ro‘yxatdan o‘ting."],
            ["To‘lov qiling", "Kurs uchun to‘lovni amalga oshiring va adminga xabar bering."],
            ["Ruxsat oling", "Admin tasdiqlashi bilan darslar kabinetingizda ochiladi."],
            ["O‘qing", "Telefon yoki kompyuterdan, o‘zingizga qulay paytda."]].map(([t, d], i) => (
            <div key={i} className="card-glass glass"><div className="num">{i + 1}</div><h3>{t}</h3><p>{d}</p></div>
          ))}
        </div>
      </section>

      <section className="section" id="savollar">
        <div className="section-head"><h2>Ko‘p so‘raladigan savollar</h2></div>
        <div className="faq">
          <details className="glass" open><summary>Kompyuterni umuman bilmasam ham o‘rgana olamanmi?</summary><p>Ha. Kurs eng boshidan, sichqoncha va klaviaturadan boshlanadi.</p></details>
          <details className="glass"><summary>Nega email so‘raladi?</summary><p>Email va parolingiz bilan akkauntingizga kirasiz. Parolingiz shifrlangan holda saqlanadi, uni hech kim ko‘ra olmaydi.</p></details>
          <details className="glass"><summary>Darslarni telefondan ko‘rsa bo‘ladimi?</summary><p>Ha, telefon, planshet yoki kompyuterdan ko‘rishingiz mumkin.</p></details>
          <details className="glass"><summary>Ro‘yxatdan o‘tgach darslar qachon ochiladi?</summary><p>To‘lov tasdiqlangach, admin sizga ruxsat beradi va darslar kabinetingizda paydo bo‘ladi.</p></details>
        </div>
      </section>

      {social?.telegram && (
        <section className="section">
          <div className="cta-band glass">
            <div><h2>Telegram kanalimizga qo‘shiling</h2><p>Bepul foydali darslar, Excel va Word bo‘yicha maslahatlar.</p></div>
            <a className="btn btn-mint btn-lg" href={social.telegram} target="_blank" rel="noopener noreferrer">{socialHandle(social.telegram)}</a>
          </div>
        </section>
      )}

      <footer className="footer" id="aloqa">
        <div className="footer-links">
          <span className="glass">kompsavod.uz</span>
          {SOCIALS.filter(([k]) => social && social[k]).map(([k, label, icon]) => (
            <a key={k} className="glass" href={social[k]} target="_blank" rel="noopener noreferrer">{icon}{label}: {socialHandle(social[k])}</a>
          ))}
          {social?.phone && <a className="glass" href={`tel:${social.phone.replace(/[^\d+]/g, "")}`}>{social.phone}</a>}
        </div>
        <small>© 2026 KompSavod</small>
      </footer>
    </div>
  );
}

/* ================= Kirish / ro'yxatdan o'tish ================= */
function Login({ me, onLogin, mode }) {
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";

  useEffect(() => {
    document.body.className = "dark-bg";
    return () => { document.body.className = ""; };
  }, []);
  useEffect(() => { if (me && me.role !== "admin") onLogin(me); }, [me]);
  useEffect(() => { setError(""); }, [mode]);

  const submit = async (e) => {
    e.preventDefault();
    if (signup) {
      if (name.trim().length < 2) { setError("Ism va familiyangizni yozing."); return; }
      if (!region) { setError("Hududingizni tanlang."); return; }
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) { setError("Email manzil noto‘g‘ri. Masalan: ism@gmail.com"); return; }
    if (password.length < (signup ? 6 : 1)) { setError(signup ? "Parol kamida 6 ta belgidan iborat bo‘lsin." : "Parolni kiriting."); return; }
    if (signup && password !== password2) { setError("Parollar bir xil emas."); return; }
    setBusy(true); setError("");
    try {
      const d = signup
        ? await api("/api/auth/signup", { method: "POST", body: { name, region, email: email.trim(), password } })
        : await api("/api/auth/login", { method: "POST", body: { email: email.trim(), password } });
      onLogin(d.user);
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <div className="auth-page">
      <div className="wrap"><nav className="nav"><a className="brand" href="#/"><Logo />KompSavod</a></nav></div>
      <main className="auth-center">
        <div className="auth-card">
          <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
            <a className={`btn btn-sm ${signup ? "btn-light" : "btn-indigo"}`} style={{ flex: 1 }} href="#/kirish">Kirish</a>
            <a className={`btn btn-sm ${signup ? "btn-indigo" : "btn-light"}`} style={{ flex: 1 }} href="#/royxat">Ro‘yxatdan o‘tish</a>
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
          <form onSubmit={submit} noValidate>
            <h1>{signup ? "Ro‘yxatdan o‘tish" : "Kirish"}</h1>
            <p className="lead">{signup ? "Ma’lumotlaringizni kiriting va parol o‘ylab toping." : "Email va parolingizni kiriting."}</p>
            {signup && (
              <>
                <div className="field">
                  <label htmlFor="name">Ism va familiya</label>
                  <input id="name" className="input" autoComplete="name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
                </div>
                <div className="field">
                  <label htmlFor="region">Hudud</label>
                  <select id="region" className="input" value={region} onChange={(e) => setRegion(e.target.value)}>
                    <option value="">Tanlang</option>
                    {REGIONS.map(r => <option key={r}>{r}</option>)}
                  </select>
                </div>
              </>
            )}
            <div className="field">
              <label htmlFor="email">Email</label>
              <input id="email" className="input" type="email" autoComplete="email" placeholder="ism@gmail.com" maxLength={120}
                value={email} onChange={(e) => setEmail(e.target.value)} autoFocus={!signup} />
            </div>
            <div className="field">
              <label htmlFor="password">Parol</label>
              <input id="password" className="input" type="password" autoComplete={signup ? "new-password" : "current-password"} maxLength={128}
                value={password} onChange={(e) => setPassword(e.target.value)} />
              {signup && <span className="hint">Kamida 6 ta belgi.</span>}
            </div>
            {signup && (
              <div className="field">
                <label htmlFor="password2">Parolni takrorlang</label>
                <input id="password2" className="input" type="password" autoComplete="new-password" maxLength={128}
                  value={password2} onChange={(e) => setPassword2(e.target.value)} />
              </div>
            )}
            <button className="btn btn-indigo" style={{ width: "100%" }} disabled={busy}>
              {busy ? "Kuting…" : (signup ? "Ro‘yxatdan o‘tish" : "Kirish")}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

/* ================= Admin kirish (login + parol) ================= */
function AdminLogin({ onLogin }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.body.className = "dark-bg";
    return () => { document.body.className = ""; };
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (!login.trim() || !password) { setError("Login va parolni kiriting."); return; }
    setBusy(true); setError("");
    try {
      const d = await api("/api/auth/admin-login", { method: "POST", body: { login: login.trim(), password } });
      onLogin(d.user);
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <div className="auth-page">
      <div className="wrap"><nav className="nav"><a className="brand" href="#/"><Logo />KompSavod</a></nav></div>
      <main className="auth-center">
        <div className="auth-card">
          {error && <div className="form-error" role="alert">{error}</div>}
          <form onSubmit={submit} noValidate>
            <h1>Admin panelga kirish</h1>
            <p className="lead">Admin login va parolingizni kiriting.</p>
            <div className="field">
              <label htmlFor="admin-login">Login</label>
              <input id="admin-login" className="input" autoComplete="username" value={login}
                onChange={(e) => setLogin(e.target.value)} autoFocus />
            </div>
            <div className="field">
              <label htmlFor="admin-password">Parol</label>
              <input id="admin-password" className="input" type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </div>
            <button className="btn btn-indigo" style={{ width: "100%" }} disabled={busy}>{busy ? "Tekshirilmoqda…" : "Kirish"}</button>
          </form>
        </div>
      </main>
    </div>
  );
}

/* ================= Ichki sahifa sarlavhasi ================= */
function AppHeader({ me, logout }) {
  return (
    <header className="app-header">
      <div className="wrap">
        <nav className="nav">
          <a className="brand" href="#/"><Logo />KompSavod</a>
          <div className="nav-links">
            <span className="user-chip">{me.name}{me.role === "admin" ? " (admin)" : ""}</span>
            <button className="btn btn-ghost btn-sm" onClick={logout}>Chiqish</button>
          </div>
        </nav>
      </div>
    </header>
  );
}

/* ================= O'quvchi kabineti ================= */
function Cabinet({ me, logout }) {
  const [courses, setCourses] = useState(null);
  const [ci, setCi] = useState(0);
  const [li, setLi] = useState(0);

  useEffect(() => {
    if (!me) { go("#/kirish"); return; }
    if (me.role === "admin") { goAdmin(); return; }
    if (me.status === "approved") api("/api/my/courses").then(d => setCourses(d.courses)).catch(() => setCourses([]));
  }, [me]);
  if (!me || me.role === "admin") return null;

  const head = (
    <>
      <h1>Salom, {me.name.split(" ")[0]}!</h1>
      <p className="lead">{me.email || fmtPhone(me.phone)} · {me.region} · ro‘yxatdan o‘tgan sana: {fmtDate(me.createdAt, false)}</p>
    </>
  );

  let body;
  if (me.status === "pending") {
    body = <div className="status-card pending"><h2>Arizangiz ko‘rib chiqilmoqda</h2><p>To‘lovingiz tasdiqlangach, admin sizga ruxsat beradi va darslar shu sahifada ochiladi. Savollar bo‘lsa, Telegram orqali yozing: <TgContact />.</p></div>;
  } else if (me.status !== "approved") {
    body = <div className={`status-card ${me.status}`}><h2>{me.status === "blocked" ? "Akkauntingiz vaqtincha yopilgan" : "Arizangiz tasdiqlanmadi"}</h2><p>Batafsil ma’lumot uchun bog‘laning: <TgContact />.</p></div>;
  } else if (courses === null) {
    body = <div className="loading">Kurslar yuklanmoqda…</div>;
  } else if (!courses.length) {
    body = <div className="status-card pending"><h2>Sizga hali kurs ochilmagan</h2><p>Admin kursga ruxsat bergach, u shu yerda paydo bo‘ladi.</p></div>;
  } else {
    const c = courses[Math.min(ci, courses.length - 1)];
    const l = c.lessons[li];
    const embed = l ? videoEmbed(l.videoUrl) : null;
    body = (
      <>
        <div className="course-pick" role="tablist" aria-label="Kurslaringiz">
          {courses.map((x, i) => <button key={x.id} className="tab" role="tab" aria-selected={i === ci} onClick={() => { setCi(i); setLi(0); }}>{x.title}</button>)}
        </div>
        <div className="player-layout">
          <div className="player-main">
            <div className="video-box">
              {embed
                ? <iframe key={embed} src={embed} title={l.title} allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin"></iframe>
                : <div className="video-empty">{c.lessons.length ? "Bu dars uchun video hali qo‘shilmagan." : "Bu kursga hali dars qo‘shilmagan."}</div>}
            </div>
            <div className="panel" style={{ marginTop: 16 }}>
              <h2 style={{ marginBottom: 6 }}>{l ? `${li + 1}. ${l.title}` : c.title}</h2>
              <p style={{ margin: 0, color: "var(--muted)", whiteSpace: "pre-line" }}>{l ? l.description : c.description}</p>
              {l && (
                <div style={{ display: "flex", gap: 10, marginTop: 16, flexWrap: "wrap" }}>
                  <button className="btn btn-light btn-sm" disabled={li === 0} onClick={() => setLi(li - 1)}>Oldingi dars</button>
                  <button className="btn btn-indigo btn-sm" disabled={li >= c.lessons.length - 1} onClick={() => setLi(li + 1)}>Keyingi dars</button>
                </div>
              )}
            </div>
          </div>
          <aside className="player-side panel">
            <h2>Darslar ({c.lessons.length})</h2>
            <ol className="lesson-list">
              {c.lessons.map((x, i) => (
                <li key={x.id}>
                  <button aria-current={i === li} onClick={() => { setLi(i); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
                    <span className="no">{i + 1}</span>
                    <span>{x.title}{x.duration && <><br /><small style={{ color: "var(--muted)" }}>{x.duration}</small></>}</span>
                  </button>
                </li>
              ))}
            </ol>
          </aside>
        </div>
      </>
    );
  }

  return (
    <>
      <AppHeader me={me} logout={logout} />
      <main className="wrap page">{head}{body}</main>
    </>
  );
}

/* ================= Admin panel ================= */
function Admin({ me, logout, notify, onLogin }) {
  const [tab, setTab] = useState("users");
  if (!me) return <AdminLogin onLogin={onLogin} />;
  if (me.role !== "admin") {
    return (
      <>
        <AppHeader me={me} logout={logout} />
        <main className="wrap page"><div className="status-card rejected"><h2>Bu sahifa faqat admin uchun</h2><p><a href="#/kabinet">Kabinetingizga qaytish</a></p></div></main>
      </>
    );
  }
  return (
    <>
      <AppHeader me={me} logout={logout} />
      <main className="wrap page">
        <h1>Admin panel</h1>
        <p className="lead">O‘quvchilar, ruxsatlar va kurslarni boshqarish.</p>
        <div className="tabs" role="tablist">
          <button className="tab" role="tab" aria-selected={tab === "users"} onClick={() => setTab("users")}>O‘quvchilar</button>
          <button className="tab" role="tab" aria-selected={tab === "courses"} onClick={() => setTab("courses")}>Kurslar va darslar</button>
          <button className="tab" role="tab" aria-selected={tab === "teacher"} onClick={() => setTab("teacher")}>O‘qituvchi</button>
        </div>
        {tab === "users" ? <AdminUsers notify={notify} /> : tab === "courses" ? <AdminCourses notify={notify} /> : <AdminTeacher notify={notify} />}
      </main>
    </>
  );
}

function AdminUsers({ notify }) {
  const [data, setData] = useState(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [accessFor, setAccessFor] = useState(null);

  const load = useCallback(() => {
    api("/api/admin/users").then(setData).catch((e) => notify(e.message, "error"));
  }, [notify]);
  useEffect(() => {
    load();
    const t = setInterval(load, 20000); // har 20 soniyada yangilanadi
    return () => clearInterval(t);
  }, [load]);

  if (!data) return <div className="loading">Yuklanmoqda…</div>;
  const { users, courses } = data;
  const count = (s) => users.filter(u => u.status === s).length;
  const qq = q.toLowerCase().replace(/\s/g, "");
  const list = users.filter(u =>
    (status === "all" || u.status === status) &&
    (!qq || u.name.toLowerCase().replace(/\s/g, "").includes(qq) || (u.email || "").includes(qq) || (u.phone || "").includes(qq)));
  const courseName = (id) => courses.find(c => c.id === id)?.title || "—";

  const setUserStatus = async (u, st, label) => {
    if (!confirm(`${u.name} ${label}?`)) return;
    try {
      await api(`/api/admin/users/${u.id}/status`, { method: "POST", body: { status: st } });
      notify("Saqlandi", "success");
      load();
    } catch (e) { notify(e.message, "error"); }
  };

  return (
    <>
      <div className="stats">
        <div className="stat"><div className="n">{users.length}</div><div className="l">Jami o‘quvchilar</div></div>
        <div className="stat"><div className="n">{count("pending")}</div><div className="l">Ruxsat kutmoqda</div></div>
        <div className="stat"><div className="n">{count("approved")}</div><div className="l">Ruxsat berilgan</div></div>
        <div className="stat"><div className="n">{count("rejected") + count("blocked")}</div><div className="l">Rad etilgan / bloklangan</div></div>
      </div>
      <div className="toolbar">
        <label className="sr-only" htmlFor="search">Qidirish</label>
        <input id="search" className="input grow" type="search" placeholder="Ism yoki email bo‘yicha qidirish" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="sr-only" htmlFor="st">Holat</label>
        <select id="st" className="input" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">Barchasi</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <a className="btn btn-light" href="/api/admin/users.csv">Excel’ga yuklab olish</a>
      </div>
      <div className="table-box">
        <table>
          <thead><tr><th>Ism</th><th>Email</th><th>Hudud</th><th>Ro‘yxatdan o‘tgan</th><th>Oxirgi kirish</th><th>Kirishlar</th><th>Holat</th><th>Kurslar</th><th>Amallar</th></tr></thead>
          <tbody>
            {list.length ? list.map(u => (
              <tr key={u.id}>
                <td><b>{u.name}</b>{u.adminNote && <><br /><small style={{ color: "var(--muted)" }}>{u.adminNote}</small></>}</td>
                <td>{u.email ? <a href={`mailto:${u.email}`}>{u.email}</a> : <a href={`tel:${u.phone}`}>{fmtPhone(u.phone)}</a>}</td>
                <td>{u.region}</td>
                <td>{fmtDate(u.createdAt)}</td>
                <td>{fmtDate(u.lastLoginAt)}</td>
                <td>{u.loginCount}</td>
                <td><span className={`badge ${u.status}`}>{STATUS_LABELS[u.status]}</span></td>
                <td style={{ whiteSpace: "normal", minWidth: 160 }}>{u.courses.map(courseName).join(", ") || "—"}</td>
                <td><div className="row-actions">
                  <button className="btn btn-indigo btn-sm" onClick={() => setAccessFor(u)}>{u.status === "approved" ? "Kurslarni o‘zgartirish" : "Ruxsat berish"}</button>
                  {u.status === "pending" && <button className="btn btn-danger btn-sm" onClick={() => setUserStatus(u, "rejected", "rad etilsinmi")}>Rad etish</button>}
                  {u.status === "approved" && <button className="btn btn-danger btn-sm" onClick={() => setUserStatus(u, "blocked", "bloklansinmi")}>Bloklash</button>}
                  {(u.status === "rejected" || u.status === "blocked") && <button className="btn btn-light btn-sm" onClick={() => setUserStatus(u, "pending", "kutish holatiga qaytarilsinmi")}>Kutishga qaytarish</button>}
                </div></td>
              </tr>
            )) : (
              <tr><td colSpan="9" style={{ textAlign: "center", padding: 32, color: "var(--muted)" }}>{users.length ? "Qidiruv bo‘yicha hech kim topilmadi." : "Hali hech kim ro‘yxatdan o‘tmagan."}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {accessFor && <AccessModal user={accessFor} courses={courses} notify={notify} onClose={() => setAccessFor(null)} onSaved={() => { setAccessFor(null); load(); }} />}
    </>
  );
}

function AccessModal({ user, courses, notify, onClose, onSaved }) {
  const [selected, setSelected] = useState(new Set(user.courses));
  const [note, setNote] = useState(user.adminNote || "");
  const [busy, setBusy] = useState(false);
  const toggle = (id) => { const s = new Set(selected); s.has(id) ? s.delete(id) : s.add(id); setSelected(s); };
  const save = async (e) => {
    e.preventDefault();
    if (!selected.size) { notify("Kamida bitta kursni belgilang", "error"); return; }
    setBusy(true);
    try {
      await api(`/api/admin/users/${user.id}/status`, { method: "POST", body: { status: "approved", courses: [...selected], note } });
      notify("Ruxsat berildi", "success");
      onSaved();
    } catch (err) { notify(err.message, "error"); setBusy(false); }
  };
  return (
    <Modal title="Ruxsat berish" onClose={onClose}>
      <form onSubmit={save}>
        <p className="lead" style={{ marginBottom: 12 }}>{user.name} · {user.email || fmtPhone(user.phone)}</p>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontWeight: 800, marginBottom: 6 }}>Qaysi kurslarga ruxsat beriladi</legend>
          {courses.length ? courses.map(c => (
            <label key={c.id} className="check">
              <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} /> {c.title}
              {!c.published && <span className="badge draft">yashirin</span>}
            </label>
          )) : <p style={{ color: "var(--muted)" }}>Avval “Kurslar va darslar” bo‘limida kurs qo‘shing.</p>}
        </fieldset>
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="note">Izoh (faqat admin ko‘radi)</label>
          <input id="note" className="input" maxLength={200} placeholder="Masalan: Click orqali to‘ladi, 12.11" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-light" onClick={onClose}>Bekor qilish</button>
          <button className="btn btn-indigo" disabled={busy}>{user.status === "approved" ? "Saqlash" : "Ruxsat berish"}</button>
        </div>
      </form>
    </Modal>
  );
}

const emptyCourse = { title: "", description: "", price: "", order: "", published: false };
const emptyLesson = { title: "", videoUrl: "", duration: "", description: "", order: "" };

function resizeImage(file, max = 800) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob(b => b ? resolve(b) : reject(new Error("Rasmni o‘qib bo‘lmadi")), "image/jpeg", 0.88);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Bu fayl rasm emas")); };
    img.src = url;
  });
}

function AdminTeacher({ notify }) {
  const [form, setForm] = useState(null);
  const [photoUrl, setPhotoUrl] = useState(null);
  const [busy, setBusy] = useState(false);

  const apply = (t) => { setForm({ name: t.name, title: t.title, bio: t.bio, years: t.years, students: t.students, show: t.show }); setPhotoUrl(t.photoUrl); };
  useEffect(() => { api("/api/teacher").then(d => apply(d.teacher)).catch(e => notify(e.message, "error")); }, [notify]);
  if (!form) return <div className="loading">Yuklanmoqda…</div>;

  const f = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const save = async (e) => {
    e.preventDefault();
    if (form.name.trim().length < 2) { notify("O‘qituvchining ismini yozing", "error"); return; }
    setBusy(true);
    try { const d = await api("/api/admin/teacher", { method: "PUT", body: form }); apply(d.teacher); notify("Saqlandi", "success"); }
    catch (err) { notify(err.message, "error"); }
    setBusy(false);
  };

  const upload = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const blob = await resizeImage(file);
      const r = await fetch("/api/admin/teacher/photo", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "image/jpeg", "X-Requested-With": "kompsavod" }, body: blob });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "Rasmni yuklab bo‘lmadi");
      setPhotoUrl(d.teacher.photoUrl);
      notify("Rasm yangilandi", "success");
    } catch (err) { notify(err.message, "error"); }
    setBusy(false);
  };

  return (
    <>
    <form className="panel" onSubmit={save} noValidate style={{ maxWidth: 760 }}>
      <h2>O‘qituvchi haqida</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>Bu ma’lumot saytning bosh sahifasida «O‘qituvchingiz bilan tanishing» bo‘limida chiqadi.</p>
      <div className="field">
        <label>Rasm</label>
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          {photoUrl ? <img className="teacher-photo-admin" src={photoUrl} alt="O‘qituvchi rasmi" /> : <span style={{ color: "var(--muted)" }}>Rasm yo‘q</span>}
          <label className="btn btn-light" style={{ cursor: "pointer" }}>
            Rasmni almashtirish
            <input type="file" accept="image/*" onChange={upload} disabled={busy} style={{ display: "none" }} />
          </label>
        </div>
      </div>
      <div className="field"><label htmlFor="tN">Ism va familiya</label><input id="tN" className="input" maxLength={80} value={form.name} onChange={f("name")} /></div>
      <div className="field"><label htmlFor="tT">Lavozimi</label><input id="tT" className="input" maxLength={120} value={form.title} onChange={f("title")} /></div>
      <div className="field"><label htmlFor="tB">O‘zi haqida</label><textarea id="tB" className="input" maxLength={1200} rows={5} value={form.bio} onChange={f("bio")}></textarea></div>
      <div className="form-row">
        <div className="field"><label htmlFor="tY">Tajriba (yil)</label><input id="tY" className="input" maxLength={20} placeholder="Masalan: 3+" value={form.years} onChange={f("years")} /><span className="hint">Bo‘sh qoldirilsa, chiqmaydi</span></div>
        <div className="field"><label htmlFor="tS">O‘quvchilar soni</label><input id="tS" className="input" maxLength={20} placeholder="Masalan: 100+" value={form.students} onChange={f("students")} /><span className="hint">Bo‘sh qoldirilsa, chiqmaydi</span></div>
      </div>
      <label className="check"><input type="checkbox" checked={!!form.show} onChange={f("show")} /> Saytda ko‘rsatilsin</label>
      <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
        <button className="btn btn-indigo" disabled={busy}>Saqlash</button>
      </div>
    </form>
    <AdminSocial notify={notify} />
    </>
  );
}

function AdminSocial({ notify }) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/teacher").then(d => setForm(d.social)).catch(e => notify(e.message, "error")); }, [notify]);
  if (!form) return null;
  const f = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { const d = await api("/api/admin/social", { method: "PUT", body: form }); setForm(d.social); notify("Saqlandi", "success"); }
    catch (err) { notify(err.message, "error"); }
    setBusy(false);
  };
  return (
    <form className="panel" onSubmit={save} noValidate style={{ maxWidth: 760, marginTop: 20 }}>
      <h2>Ijtimoiy tarmoqlar va aloqa</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>Havolalar o‘qituvchi bo‘limida va sahifa pastida chiqadi. Bo‘sh qoldirilgani saytda ko‘rinmaydi.</p>
      <div className="field"><label htmlFor="sI">Instagram</label><input id="sI" className="input" maxLength={300} placeholder="https://www.instagram.com/username" value={form.instagram} onChange={f("instagram")} /></div>
      <div className="field"><label htmlFor="sT">Telegram</label><input id="sT" className="input" maxLength={300} placeholder="https://t.me/username" value={form.telegram} onChange={f("telegram")} /></div>
      <div className="field"><label htmlFor="sY">YouTube</label><input id="sY" className="input" maxLength={300} placeholder="https://www.youtube.com/@kanal" value={form.youtube} onChange={f("youtube")} /></div>
      <div className="field"><label htmlFor="sP">Telefon (ixtiyoriy)</label><input id="sP" className="input" maxLength={30} placeholder="+998 90 123 45 67" value={form.phone} onChange={f("phone")} /></div>
      <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
        <button className="btn btn-indigo" disabled={busy}>Saqlash</button>
      </div>
    </form>
  );
}

function AdminCourses({ notify }) {
  const [courses, setCourses] = useState(null);
  const [selId, setSelId] = useState(null);
  const [form, setForm] = useState(emptyCourse);
  const [lessons, setLessons] = useState([]);
  const [lForm, setLForm] = useState(emptyLesson);
  const [lEditId, setLEditId] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadCourses = useCallback(() => api("/api/admin/courses").then(d => { setCourses(d.courses); return d.courses; }).catch(e => notify(e.message, "error")), [notify]);
  const loadLessons = useCallback((id) => api(`/api/admin/courses/${id}/lessons`).then(d => setLessons(d.lessons)).catch(e => notify(e.message, "error")), [notify]);
  useEffect(() => { loadCourses(); }, [loadCourses]);

  const select = (c) => {
    setSelId(c ? c.id : null);
    setForm(c ? { title: c.title, description: c.description, price: c.price, order: c.order, published: c.published } : { ...emptyCourse, order: (courses?.length || 0) + 1 });
    setLEditId(null);
    setLessons([]);
    if (c) { loadLessons(c.id); setLForm(emptyLesson); }
  };

  const saveCourse = async (e) => {
    e.preventDefault();
    if (form.title.trim().length < 2) { notify("Kurs nomini yozing", "error"); return; }
    setBusy(true);
    try {
      if (selId) {
        await api(`/api/admin/courses/${selId}`, { method: "PUT", body: form });
        notify("Kurs saqlandi", "success");
        loadCourses();
      } else {
        const d = await api("/api/admin/courses", { method: "POST", body: form });
        notify("Kurs qo‘shildi. Endi darslarni qo‘shing.", "success");
        const list = await loadCourses();
        select(list.find(c => c.id === d.id));
      }
    } catch (err) { notify(err.message, "error"); }
    setBusy(false);
  };

  const saveLesson = async (e) => {
    e.preventDefault();
    if (lForm.title.trim().length < 2) { notify("Dars nomini yozing", "error"); return; }
    if (lForm.videoUrl && !videoEmbed(lForm.videoUrl)) { notify("Faqat YouTube yoki Vimeo havolasi qabul qilinadi", "error"); return; }
    setBusy(true);
    try {
      if (lEditId) await api(`/api/admin/lessons/${lEditId}`, { method: "PUT", body: lForm });
      else await api(`/api/admin/courses/${selId}/lessons`, { method: "POST", body: lForm });
      notify("Dars saqlandi", "success");
      setLEditId(null);
      setLForm(emptyLesson);
      loadLessons(selId);
      loadCourses();
    } catch (err) { notify(err.message, "error"); }
    setBusy(false);
  };

  const toggleHidden = async (l) => {
    try {
      await api(`/api/admin/lessons/${l.id}`, { method: "PUT", body: { ...l, hidden: !l.hidden } });
      loadLessons(selId);
      loadCourses();
    } catch (err) { notify(err.message, "error"); }
  };

  if (!courses) return <div className="loading">Yuklanmoqda…</div>;
  const f = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const lf = (k) => (e) => setLForm({ ...lForm, [k]: e.target.value });
  const showForm = selId !== null || form !== emptyCourse;

  return (
    <div className="player-layout">
      <div className="player-side">
        <div className="panel">
          <h2>Kurslar</h2>
          {courses.length ? (
            <ol className="lesson-list">
              {courses.map(c => (
                <li key={c.id}><button aria-current={c.id === selId} onClick={() => select(c)}>
                  <span className="no">{c.order}</span>
                  <span>{c.title}<br /><small style={{ color: "var(--muted)" }}>{c.lessonCount} dars · {c.published ? "saytda" : "yashirin"}</small></span>
                </button></li>
              ))}
            </ol>
          ) : <p style={{ color: "var(--muted)", margin: "0 0 12px" }}>Hali kurs yo‘q. Birinchi kursni qo‘shing.</p>}
          <button className="btn btn-mint" style={{ width: "100%", marginTop: 12 }} onClick={() => select(null)}>Yangi kurs qo‘shish</button>
        </div>
      </div>
      <div className="player-main">
        {!showForm ? (
          <div className="panel"><p style={{ margin: 0, color: "var(--muted)" }}>Tahrirlash uchun chapdan kursni tanlang yoki yangi kurs qo‘shing.</p></div>
        ) : (
          <form className="panel" onSubmit={saveCourse} noValidate>
            <h2>{selId ? "Kursni tahrirlash" : "Yangi kurs"}</h2>
            <div className="field"><label htmlFor="cT">Kurs nomi</label><input id="cT" className="input" maxLength={120} value={form.title} onChange={f("title")} /></div>
            <div className="field"><label htmlFor="cD">Qisqacha tavsif</label><textarea id="cD" className="input" maxLength={600} value={form.description} onChange={f("description")}></textarea></div>
            <div className="form-row">
              <div className="field"><label htmlFor="cP">Narxi</label><input id="cP" className="input" maxLength={60} placeholder="Masalan: 299 000 so‘m" value={form.price} onChange={f("price")} /></div>
              <div className="field"><label htmlFor="cO">Tartib raqami</label><input id="cO" className="input" type="number" min="0" value={form.order} onChange={f("order")} /><span className="hint">Saytda shu tartibda chiqadi</span></div>
            </div>
            <label className="check"><input type="checkbox" checked={!!form.published} onChange={f("published")} /> Saytda ko‘rsatilsin</label>
            <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
              <button className="btn btn-indigo" disabled={busy}>{selId ? "Saqlash" : "Kursni qo‘shish"}</button>
            </div>
          </form>
        )}

        {selId && (
          <div className="panel">
            <h2>Darslar</h2>
            {lessons.length ? (
              <div className="table-box" style={{ marginBottom: 20 }}>
                <table>
                  <thead><tr><th>#</th><th>Dars nomi</th><th>Video</th><th>Holat</th><th>Amallar</th></tr></thead>
                  <tbody>
                    {lessons.map(l => (
                      <tr key={l.id}>
                        <td>{l.order}</td>
                        <td style={{ whiteSpace: "normal" }}><b>{l.title}</b>{l.duration && <><br /><small style={{ color: "var(--muted)" }}>{l.duration}</small></>}</td>
                        <td>{videoEmbed(l.videoUrl) ? "Bor" : <span className="badge rejected">Yo‘q</span>}</td>
                        <td>{l.hidden ? <span className="badge draft">yashirin</span> : <span className="badge approved">ko‘rinadi</span>}</td>
                        <td><div className="row-actions">
                          <button className="btn btn-light btn-sm" onClick={() => { setLEditId(l.id); setLForm({ title: l.title, videoUrl: l.videoUrl, duration: l.duration, description: l.description, order: l.order }); }}>Tahrirlash</button>
                          <button className="btn btn-light btn-sm" onClick={() => toggleHidden(l)}>{l.hidden ? "Ko‘rsatish" : "Yashirish"}</button>
                        </div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p style={{ color: "var(--muted)" }}>Bu kursda hali dars yo‘q.</p>}

            <form onSubmit={saveLesson} noValidate>
              <h2 style={{ fontSize: 18 }}>{lEditId ? "Darsni tahrirlash" : "Yangi dars qo‘shish"}</h2>
              <div className="form-row">
                <div className="field"><label htmlFor="lT">Dars nomi</label><input id="lT" className="input" maxLength={160} value={lForm.title} onChange={lf("title")} /></div>
                <div className="field"><label htmlFor="lO">Tartib raqami</label><input id="lO" className="input" type="number" min="1" placeholder={String(lessons.length + 1)} value={lForm.order} onChange={lf("order")} /></div>
              </div>
              <div className="field"><label htmlFor="lU">Video havola (YouTube yoki Vimeo)</label><input id="lU" className="input" type="url" placeholder="https://youtu.be/..." value={lForm.videoUrl} onChange={lf("videoUrl")} /><span className="hint">YouTube’da videoni “Unlisted” (havola orqali) qilib yuklang.</span></div>
              <div className="field"><label htmlFor="lDu">Davomiyligi</label><input id="lDu" className="input" maxLength={20} placeholder="12 daqiqa" value={lForm.duration} onChange={lf("duration")} /></div>
              <div className="field"><label htmlFor="lDe">Dars tavsifi yoki topshiriq</label><textarea id="lDe" className="input" maxLength={2000} value={lForm.description} onChange={lf("description")}></textarea></div>
              <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
                <button className="btn btn-indigo" disabled={busy}>{lEditId ? "Saqlash" : "Darsni qo‘shish"}</button>
                {lEditId && <button type="button" className="btn btn-light" onClick={() => { setLEditId(null); setLForm(emptyLesson); }}>Bekor qilish</button>}
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);

</script>
</body>
</html>
"""

# ------------------------------------------------------------------
# Ishga tushirish
# ------------------------------------------------------------------
init_db()

if __name__ == "__main__":
    threading.Thread(target=_backup_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    print("=" * 60)
    print(" KompSavod serveri ishga tushdi")
    print(f" Manzil:      http://localhost:{port}")
    print(f" Baza:        {DB_PATH}")
    print(f" Zaxira:      {BACKUP_DIR} (har {BACKUP_EVERY_HOURS} soatda)")
    print(" Kirish:      email + parol (ro'yxatdan o'tish: /#/royxat)")
    print(f" Adminlar:    {', '.join(sorted(ADMIN_PHONES)) or 'yo‘q – ADMIN_PHONE ni belgilang!'}")
    print(f" Admin panel: http://localhost:{port}/admin  (login: {ADMIN_LOGIN}, parol: "
          f"{'ADMIN_PASSWORD dan' if os.environ.get('ADMIN_PASSWORD', '').strip() else ADMIN_PASSWORD_FILE + ' faylida'})")
    print("=" * 60)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
