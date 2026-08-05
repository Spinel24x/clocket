"""
VLESS Panel - Professional Edition
Modular architecture with proper error handling and logging
"""

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
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote_plus, urlparse

# ==================== Configuration ====================
class Config:
    CF_DOMAIN = os.getenv("CF_DOMAIN", "")
    USER_PASS = os.getenv("USER_PASS", "admin123")
    DB_PATH = "/app/data/panel.db"
    CONFIG_PATH = "/app/configs/xray.json"
    XRAY_BINARY = "/usr/local/bin/xray"
    XRAY_PORT = 10000
    API_PORT = 8000
    NGINX_PORT = 8080

# ==================== Logging ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

logger.info(f"Starting VLESS Panel")
logger.info(f"CF_DOMAIN: {Config.CF_DOMAIN or 'NOT SET'}")
logger.info(f"USER_PASS: {Config.USER_PASS}")

# ==================== Database Manager ====================
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_conn()
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
        logger.info("Database initialized")

    def get_all_configs(self):
        conn = self.get_conn()
        rows = conn.execute("SELECT * FROM configs WHERE enabled = 1 ORDER BY created_at DESC").fetchall()
        conn.close()
        return rows

    def add_config(self, uid, name, remarks):
        conn = self.get_conn()
        conn.execute(
            "INSERT INTO configs (uuid, name, remarks) VALUES (?, ?, ?)",
            (uid, name, remarks)
        )
        conn.commit()
        conn.close()

    def delete_config(self, config_id):
        conn = self.get_conn()
        conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))
        conn.commit()
        conn.close()

    def toggle_config(self, config_id):
        conn = self.get_conn()
        row = conn.execute("SELECT enabled FROM configs WHERE id = ?", (config_id,)).fetchone()
        if row:
            new_status = 0 if row["enabled"] else 1
            conn.execute("UPDATE configs SET enabled = ? WHERE id = ?", (new_status, config_id))
            conn.commit()
        conn.close()

# ==================== VLESS Link Generator ====================
class VlessLinkGenerator:
    @staticmethod
    def generate(uuid_str, remarks=""):
        if not Config.CF_DOMAIN:
            return ""
        remark = remarks or "VLESS"
        return (
            f"vless://{uuid_str}@{Config.CF_DOMAIN}:443"
            f"?encryption=none&security=tls&sni={Config.CF_DOMAIN}"
            f"&fp=chrome&type=ws&host={Config.CF_DOMAIN}"
            f"&path=%2Fws#{remark}"
        )

# ==================== Xray Manager ====================
class XrayManager:
    def __init__(self, db: Database):
        self.db = db
        self.config_path = Config.CONFIG_PATH
        self.process = None

    def build_config(self):
        configs = self.db.get_all_configs()
        clients = [{"id": c["uuid"], "flow": "xtls-rprx-vision"} for c in configs]

        if not clients:
            clients = [{"id": str(uuid.uuid4()), "flow": "xtls-rprx-vision"}]

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

        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Xray config built with {len(clients)} clients")
        return config

    def start(self):
        self.build_config()
        subprocess.run(["killall", "-f", "xray run"], check=False)
        time.sleep(1)

        self.process = subprocess.Popen(
            [Config.XRAY_BINARY, "run", "-config", self.config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("Xray started")

    def restart(self):
        self.start()

    def is_running(self):
        if self.process:
            return self.process.poll() is None
        return subprocess.run(["pgrep", "-f", "xray run"], capture_output=True).returncode == 0

# ==================== Session Manager ====================
class SessionManager:
    def __init__(self):
        self.sessions = {}

    def create(self):
        session_id = secrets.token_hex(32)
        self.sessions[session_id] = True
        return session_id

    def is_valid(self, session_id):
        return session_id in self.sessions

    def destroy(self, session_id):
        self.sessions.pop(session_id, None)

# ==================== HTTP API Handler ====================
class APIHandler(BaseHTTPRequestHandler):
    db = Database(Config.DB_PATH)
    xray = XrayManager(db)
    sessions = SessionManager()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _get_cookies(self):
        cookie_str = self.headers.get("Cookie", "")
        cookies = {}
        for item in cookie_str.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                cookies[k] = v
        return cookies

    def _get_form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        form = {}
        for item in body.split("&"):
            if "=" in item:
                k, v = item.split("=", 1)
                form[unquote_plus(k)] = unquote_plus(v)
        return form

    def _require_auth(self):
        cookies = self._get_cookies()
        session_id = cookies.get("session_id", "")
        if not self.sessions.is_valid(session_id):
            self._send_json({"error": "Unauthorized"}, 401)
            return False
        return True

    def log_message(self, format, *args):
        logger.info(f"{self.path}: {format % args}")

    # ==================== Routing ====================
    def do_GET(self):
        path = urlparse(self.path).path

        routes = {
            "/health": self._handle_health,
            "/api/me": self._handle_me,
            "/api/configs": self._handle_list_configs,
        }

        if path in routes:
            routes[path]()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        routes = {
            "/api/login": self._handle_login,
            "/api/logout": self._handle_logout,
            "/api/configs": self._handle_create_config,
        }

        if path in routes:
            routes[path]()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/configs/"):
            self._handle_delete_config(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if "/toggle" in path:
            self._handle_toggle_config(path)
        else:
            self._send_json({"error": "Not found"}, 404)

    # ==================== Handlers ====================
    def _handle_health(self):
        self._send_json({
            "status": "ok",
            "xray": "running" if self.xray.is_running() else "stopped",
            "cf_domain": Config.CF_DOMAIN or "not set",
            "timestamp": datetime.now().isoformat()
        })

    def _handle_me(self):
        if self._require_auth():
            self._send_json({"logged_in": True})

    def _handle_login(self):
        form = self._get_form()
        password = form.get("password", "")

        if password != Config.USER_PASS:
            logger.warning(f"Failed login attempt")
            self._send_json({"detail": "Wrong password"}, 401)
            return

        session_id = self.sessions.create()
        logger.info("Login successful")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True}).encode())

    def _handle_logout(self):
        cookies = self._get_cookies()
        session_id = cookies.get("session_id", "")
        self.sessions.destroy(session_id)
        self._send_json({"success": True})

    def _handle_create_config(self):
        if not self._require_auth():
            return

        form = self._get_form()
        uid = str(uuid.uuid4())
        name = form.get("name", "Unnamed")
        remarks = form.get("remarks", "")

        self.db.add_config(uid, name, remarks)
        self.xray.restart()

        link = VlessLinkGenerator.generate(uid, remarks)
        logger.info(f"Config created: {uid}")

        self._send_json({
            "success": True,
            "uuid": uid,
            "name": name,
            "remarks": remarks,
            "link": link,
            "domain_set": bool(Config.CF_DOMAIN)
        })

    def _handle_list_configs(self):
        if not self._require_auth():
            return

        rows = self.db.get_all_configs()
        configs = [{
            "id": r["id"],
            "uuid": r["uuid"],
            "name": r["name"],
            "remarks": r["remarks"],
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
            "vless_link": VlessLinkGenerator.generate(r["uuid"], r["remarks"]),
            "domain_set": bool(Config.CF_DOMAIN)
        } for r in rows]

        self._send_json(configs)

    def _handle_delete_config(self, path):
        if not self._require_auth():
            return

        try:
            config_id = int(path.split("/")[3])
            self.db.delete_config(config_id)
            self.xray.restart()
            self._send_json({"success": True})
        except (IndexError, ValueError):
            self._send_json({"error": "Invalid ID"}, 400)

    def _handle_toggle_config(self, path):
        if not self._require_auth():
            return

        try:
            config_id = int(path.split("/")[3])
            self.db.toggle_config(config_id)
            self.xray.restart()
            self._send_json({"success": True})
        except (IndexError, ValueError):
            self._send_json({"error": "Invalid ID"}, 400)


# ==================== Main ====================
def main():
    # Start Xray first
    db = Database(Config.DB_PATH)
    xray = XrayManager(db)
    xray.start()

    # Start API server
    logger.info(f"API Server starting on 0.0.0.0:{Config.API_PORT}")
    server = HTTPServer(("0.0.0.0", Config.API_PORT), APIHandler)
    
    def shutdown(sig, frame):
        logger.info("Shutting down...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Server is ready!")
    server.serve_forever()


if __name__ == "__main__":
    main()
