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
    # Railway به‌طور خودکار این متغیر را برابر دامنهٔ عمومی پروژه قرار می‌دهد
    PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", os.getenv("PUBLIC_DOMAIN", ""))
    USER_PASS = os.getenv("USER_PASS", "admin123")
    DB_PATH = "/app/data/panel.db"
    CONFIG_PATH = "/app/configs/xray.json"
    XRAY_BINARY = "/usr/local/bin/xray"
    XRAY_PORT = 10000
    API_PORT = 8000

# ==================== Logging ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

logger.info(f"VLESS Panel Starting")
logger.info(f"Public Domain: {Config.PUBLIC_DOMAIN or 'NOT SET'}")

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
def make_vless(uid, remarks=""):
    """تولید لینک VLESS با دامنهٔ Railway"""
    if not Config.PUBLIC_DOMAIN:
        return ""
    remark = remarks or "VLESS"
    return (
        f"vless://{uid}@{Config.PUBLIC_DOMAIN}:443"
        f"?encryption=none&security=tls&sni={Config.PUBLIC_DOMAIN}"
        f"&fp=chrome&type=ws&host={Config.PUBLIC_DOMAIN}"
        f"&path=%2Fws#{remark}"
    )

# ==================== Xray Manager ====================
xray_process = None

def build_xray_config():
    """ساخت فایل کانفیگ Xray با کلاینت‌های فعال"""
    rows = db.get_all()
    clients = [{"id": r["uuid"]} for r in rows]  # بدون flow
    if not clients:
        clients = [{"id": str(uuid.uuid4())}]

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": Config.XRAY_PORT,
            "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {
                "network": "ws",
                "security": "none",
                "wsSettings": {"path": "/ws"}
            }
        }],
        "outbounds": [{"protocol": "freedom", "settings": {}}]
    }

    Path(Config.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(Config.CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    logger.info(f"Xray config built with {len(clients)} clients")

def start_xray():
    """اجرای Xray با مدیریت صحیح فرآیند"""
    global xray_process
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

# اجرای اولیه Xray
start_xray()

# ==================== Session Manager ====================
sessions = {}

# ==================== HTTP API Handler ====================
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

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

    def log_message(self, format, *args):
        logger.info(f"{self.path}: {format % args}")

    # -------------------- GET --------------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json({
                "status": "ok",
                "xray": "running" if is_xray_running() else "stopped",
                "domain": Config.PUBLIC_DOMAIN or "not set"
            })
            return

        if path == "/api/me":
            self._send_json({"logged_in": self._is_authenticated()}, 
                            200 if self._is_authenticated() else 401)
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
                "vless_link": make_vless(r["uuid"], r["remarks"]),
                "domain_set": bool(Config.PUBLIC_DOMAIN)
            } for r in rows]
            self._send_json(configs)
            return

        self._send_json({"error": "Not found"}, 404)

    # -------------------- POST --------------------
    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            password = self._parse_form().get("password", "")
            if password != Config.USER_PASS:
                self._send_json({"detail": "Wrong password"}, 401)
                return
            session_id = secrets.token_hex(32)
            sessions[session_id] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            logger.info("Login successful")
            return

        if path == "/api/logout":
            session_id = self._parse_cookies().get("session_id", "")
            sessions.pop(session_id, None)
            self._send_json({"success": True})
            return

        if path == "/api/configs":
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return
            form = self._parse_form()
            new_uuid = str(uuid.uuid4())
            name = form.get("name", "Unnamed")
            remarks = form.get("remarks", "")
            db.add(new_uuid, name, remarks)
            restart_xray()
            link = make_vless(new_uuid, remarks)
            self._send_json({
                "success": True,
                "uuid": new_uuid,
                "link": link,
                "domain_set": bool(Config.PUBLIC_DOMAIN)
            })
            logger.info(f"Config created: {new_uuid}")
            return

        self._send_json({"error": "Not found"}, 404)

    # -------------------- DELETE --------------------
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
                self._send_json({"success": True})
            except:
                self._send_json({"error": "Invalid ID"}, 400)
            return
        self._send_json({"error": "Not found"}, 404)

    # -------------------- PATCH --------------------
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
                self._send_json({"success": True})
            except:
                self._send_json({"error": "Invalid ID"}, 400)
            return
        self._send_json({"error": "Not found"}, 404)

# ==================== Main ====================
def main():
    logger.info(f"API server starting on 0.0.0.0:{Config.API_PORT}")
    server = HTTPServer(("0.0.0.0", Config.API_PORT), Handler)

    def shutdown(signal, frame):
        logger.info("Shutting down...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Server is ready!")
    server.serve_forever()

if __name__ == "__main__":
    main()
