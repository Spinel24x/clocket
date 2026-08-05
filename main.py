import os
import json
import uuid
import sqlite3
import subprocess
import secrets
import hashlib
import time
import signal
import sys
import logging
import threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote_plus, urlparse

# ==================== Configuration ====================
class Config:
    # دامنه Railway (برای TLS و host)
    RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", os.getenv("PUBLIC_DOMAIN", ""))
    # دامنه Worker (برای IP تمیز و پراکسی)
    WORKER_DOMAIN = os.getenv("CF_DOMAIN", "")
    USER_PASS = os.getenv("USER_PASS", "admin123")
    DB_PATH = "/app/data/panel.db"
    CONFIG_PATH = "/app/configs/xray.json"
    XRAY_BINARY = "/usr/local/bin/xray"
    XRAY_PORT = 10000
    API_PORT = 8000

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("VLESS")
logger.info(f"Railway: {Config.RAILWAY_DOMAIN or 'NOT SET'}")
logger.info(f"Worker: {Config.WORKER_DOMAIN or 'NOT SET'}")

# ==================== Database ====================
class Database:
    def __init__(self):
        self.db_path = Config.DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT 'Unnamed',
                remarks TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
        """)
        conn.commit()
        conn.close()
        logger.info("Database ready")

    def get_all(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM configs WHERE enabled = 1 ORDER BY created_at DESC").fetchall()
        conn.close()
        return rows

    def add(self, uid, name, remarks):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO configs (uuid, name, remarks) VALUES (?, ?, ?)", (uid, name, remarks))
        conn.commit()
        conn.close()

    def delete(self, config_id):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))
        conn.commit()
        conn.close()

    def toggle(self, config_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT enabled FROM configs WHERE id = ?", (config_id,)).fetchone()
        if row:
            conn.execute("UPDATE configs SET enabled = ? WHERE id = ?", (0 if row["enabled"] else 1, config_id))
            conn.commit()
        conn.close()

db = Database()

# ==================== VLESS Link Generator ====================
def make_vless(uid, remarks="", address=""):
    """
    تولید لینک VLESS با معماری دوگانه:
    - sni و host همیشه روی Railway Domain (برای TLS)
    - address می‌تونه Worker Domain، Railway Domain، یا IP تمیز باشه
    """
    sni_host = Config.RAILWAY_DOMAIN
    if not sni_host:
        return ""

    # اولویت آدرس: دستی > Worker > Railway
    if address and address.strip():
        dest = address.strip()
    elif Config.WORKER_DOMAIN:
        dest = Config.WORKER_DOMAIN
    else:
        dest = sni_host

    remark = remarks or "VLESS"
    return (
        f"vless://{uid}@{dest}:443"
        f"?encryption=none&security=tls&sni={sni_host}"
        f"&fp=chrome&type=ws&host={sni_host}"
        f"&path=%2Fws#{remark}"
    )

# ==================== Xray Manager ====================
xray_process = None

def build_xray_config():
    """ساخت کانفیگ Xray با پشتیبانی از هر دو دامنه"""
    rows = db.get_all()
    clients = [{"id": r["uuid"]} for r in rows]
    if not clients:
        clients = [{"id": str(uuid.uuid4())}]

    inbounds = []

    # Inbound اصلی: Railway Domain (همیشه فعال)
    inbounds.append({
        "listen": "127.0.0.1",
        "port": Config.XRAY_PORT,
        "protocol": "vless",
        "settings": {"clients": clients, "decryption": "none"},
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "wsSettings": {
                "path": "/ws",
                "host": Config.RAILWAY_DOMAIN
            }
        },
        "tag": "inbound-railway"
    })

    # Inbound دوم: Worker Domain (اگر تعریف شده باشه)
    if Config.WORKER_DOMAIN and Config.WORKER_DOMAIN != Config.RAILWAY_DOMAIN:
        inbounds.append({
            "listen": "127.0.0.1",
            "port": Config.XRAY_PORT + 1,
            "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {
                "network": "ws",
                "security": "none",
                "wsSettings": {
                    "path": "/ws",
                    "host": Config.WORKER_DOMAIN
                }
            },
            "tag": "inbound-worker"
        })

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [
            {"protocol": "freedom", "settings": {}, "tag": "direct"}
        ]
    }

    Path(Config.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(Config.CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    
    logger.info(f"Xray config: {len(clients)} clients, {len(inbounds)} inbounds")
    return config

def start_xray():
    """اجرای Xray با مدیریت صحیح فرآیند"""
    global xray_process
    
    # توقف Xray قبلی
    if xray_process and xray_process.poll() is None:
        try:
            xray_process.terminate()
            xray_process.wait(timeout=3)
        except:
            try:
                xray_process.kill()
            except:
                pass

    build_xray_config()
    
    xray_process = subprocess.Popen(
        [Config.XRAY_BINARY, "run", "-config", Config.CONFIG_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )

    def log_reader():
        if xray_process and xray_process.stdout:
            for line in xray_process.stdout:
                logger.info(f"XRAY: {line.decode().strip()}")

    threading.Thread(target=log_reader, daemon=True).start()
    logger.info("Xray started")

def restart_xray():
    start_xray()

def is_xray_running():
    return xray_process is not None and xray_process.poll() is None

# شروع اولیه Xray
start_xray()

# ==================== Session Manager ====================
sessions = {}

# ==================== HTTP API Handler ====================
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _parse_cookies(self):
        cookies = {}
        cookie_header = self.headers.get("Cookie", "")
        for item in cookie_header.split(";"):
            if "=" in item:
                key, value = item.strip().split("=", 1)
                cookies[key] = value
        return cookies

    def _parse_form(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            form = {}
            for item in body.split("&"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    form[unquote_plus(key)] = unquote_plus(value)
            return form
        except:
            return {}

    def _is_authenticated(self):
        session_id = self._parse_cookies().get("session_id", "")
        return session_id in sessions

    def _set_auth_cookie(self, session_id):
        self.send_header(
            "Set-Cookie",
            f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400"
        )

    def log_message(self, format, *args):
        logger.info(f"{self.command} {self.path}: {format % args}")

    # ==================== OPTIONS (CORS) ====================
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ==================== GET ====================
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json({
                "status": "ok",
                "xray": "running" if is_xray_running() else "stopped",
                "railway_domain": Config.RAILWAY_DOMAIN or "not set",
                "worker_domain": Config.WORKER_DOMAIN or "not set",
                "timestamp": datetime.now().isoformat()
            })
            return

        if path == "/api/me":
            if self._is_authenticated():
                self._send_json({"logged_in": True})
            else:
                self._send_json({"logged_in": False}, 401)
            return

        if path == "/api/configs":
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return

            rows = db.get_all()
            configs = [{
                "id": r["id"],
                "uuid": r["uuid"],
                "name": r["name"],
                "remarks": r["remarks"],
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
                "vless_link": make_vless(r["uuid"], r["remarks"]),
                "railway_domain": Config.RAILWAY_DOMAIN,
                "worker_domain": Config.WORKER_DOMAIN
            } for r in rows]
            self._send_json(configs)
            return

        if path == "/api/domains":
            self._send_json({
                "railway": Config.RAILWAY_DOMAIN,
                "worker": Config.WORKER_DOMAIN
            })
            return

        # 404
        self._send_json({"error": "Not found"}, 404)

    # ==================== POST ====================
    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            form = self._parse_form()
            password = form.get("password", "")

            if password != Config.USER_PASS:
                logger.warning("Login failed: wrong password")
                self._send_json({"detail": "Wrong password"}, 401)
                return

            session_id = secrets.token_hex(32)
            sessions[session_id] = True

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._set_auth_cookie(session_id)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            logger.info("Login successful")
            return

        if path == "/api/logout":
            session_id = self._parse_cookies().get("session_id", "")
            sessions.pop(session_id, None)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session_id=; Path=/; Max-Age=0")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        if path == "/api/configs":
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return

            form = self._parse_form()
            new_uuid = str(uuid.uuid4())
            name = form.get("name", "Unnamed")
            remarks = form.get("remarks", "")
            address = form.get("address", "")

            db.add(new_uuid, name, remarks)
            restart_xray()

            link = make_vless(new_uuid, remarks, address)
            logger.info(f"Config created: {new_uuid} -> {link}")

            self._send_json({
                "success": True,
                "uuid": new_uuid,
                "name": name,
                "remarks": remarks,
                "link": link,
                "railway_domain": Config.RAILWAY_DOMAIN,
                "worker_domain": Config.WORKER_DOMAIN
            })
            return

        # 404
        self._send_json({"error": "Not found"}, 404)

    # ==================== PUT ====================
    def do_PUT(self):
        path = urlparse(self.path).path

        if path.startswith("/api/configs/"):
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return

            try:
                config_id = int(path.split("/")[3])
                form = self._parse_form()
                name = form.get("name", "")
                remarks = form.get("remarks", "")

                conn = sqlite3.connect(Config.DB_PATH)
                conn.execute(
                    "UPDATE configs SET name = ?, remarks = ? WHERE id = ?",
                    (name, remarks, config_id)
                )
                conn.commit()
                conn.close()

                restart_xray()
                self._send_json({"success": True})
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid config ID"}, 400)
            return

        self._send_json({"error": "Not found"}, 404)

    # ==================== DELETE ====================
    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/api/configs/"):
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return

            try:
                config_id = int(path.split("/")[3])
                db.delete(config_id)
                restart_xray()
                logger.info(f"Config deleted: {config_id}")
                self._send_json({"success": True})
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid config ID"}, 400)
            return

        self._send_json({"error": "Not found"}, 404)

    # ==================== PATCH ====================
    def do_PATCH(self):
        path = urlparse(self.path).path

        if "/toggle" in path:
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return

            try:
                config_id = int(path.split("/")[3])
                db.toggle(config_id)
                restart_xray()
                logger.info(f"Config toggled: {config_id}")
                self._send_json({"success": True})
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid config ID"}, 400)
            return

        self._send_json({"error": "Not found"}, 404)


# ==================== Main ====================
def main():
    logger.info(f"API Server starting on 0.0.0.0:{Config.API_PORT}")
    logger.info(f"Railway Domain: {Config.RAILWAY_DOMAIN}")
    logger.info(f"Worker Domain: {Config.WORKER_DOMAIN}")
    
    server = HTTPServer(("0.0.0.0", Config.API_PORT), Handler)

    def shutdown(sig, frame):
        logger.info("Shutting down server...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Server is ready! Accepting connections...")
    server.serve_forever()


if __name__ == "__main__":
    main()
