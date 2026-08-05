import os
import json
import uuid
import sqlite3
import subprocess
import secrets
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote_plus

CF_DOMAIN = os.getenv("CF_DOMAIN", "")
USER_PASS = os.getenv("USER_PASS", "admin123")
DB_PATH = "/app/data/panel.db"
CONFIG_PATH = "/app/configs/xray.json"
XRAY_PORT = 10000

print(f"CF_DOMAIN: {CF_DOMAIN}")
print(f"USER_PASS: {USER_PASS}")

# ==================== Database ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            remarks TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ==================== VLESS Link Generator ====================
def generate_vless_link(config_uuid, remarks=""):
    if not CF_DOMAIN:
        return ""
    remark_text = remarks or "VLESS"
    return (
        f"vless://{config_uuid}@{CF_DOMAIN}:443"
        f"?encryption=none&security=tls&sni={CF_DOMAIN}"
        f"&fp=chrome&type=ws&host={CF_DOMAIN}"
        f"&path=%2Fws#{remark_text}"
    )

# ==================== Xray Manager ====================
def generate_xray_config():
    conn = get_db()
    configs = conn.execute("SELECT * FROM configs WHERE enabled = 1").fetchall()
    conn.close()

    clients = []
    for conf in configs:
        clients.append({"id": conf["uuid"], "flow": "xtls-rprx-vision"})

    if not clients:
        clients = [{"id": str(uuid.uuid4()), "flow": "xtls-rprx-vision"}]

    xray_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": XRAY_PORT,
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

    Path("/app/configs").mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(xray_config, f, indent=2)

    return xray_config

def restart_xray():
    subprocess.run(["pkill", "-f", "xray run"], check=False)
    time.sleep(1)
    generate_xray_config()
    subprocess.Popen(
        ["/usr/local/bin/xray", "run", "-config", CONFIG_PATH],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print("Xray restarted")

restart_xray()

# ==================== Session ====================
sessions = {}

# ==================== API Handler ====================
class APIHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _parse_cookies(self):
        cookie_header = self.headers.get("Cookie", "")
        cookies = {}
        for item in cookie_header.split(";"):
            if "=" in item:
                key, value = item.strip().split("=", 1)
                cookies[key] = value
        return cookies

    def _parse_form(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        form = {}
        for item in body.split("&"):
            if "=" in item:
                key, value = item.split("=", 1)
                form[unquote_plus(key)] = unquote_plus(value)
        return form

    def do_POST(self):
        if self.path == "/api/login":
            form = self._parse_form()
            password = form.get("password", "")
            if password != USER_PASS:
                self._send_json({"detail": "Wrong password"}, 401)
                return
            session_id = secrets.token_hex(32)
            sessions[session_id] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"session_id={session_id}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        if self.path == "/api/configs":
            cookies = self._parse_cookies()
            if cookies.get("session_id", "") not in sessions:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            form = self._parse_form()
            new_uuid = str(uuid.uuid4())
            name = form.get("name", "Unnamed")
            remarks = form.get("remarks", "")
            conn = get_db()
            conn.execute("INSERT INTO configs (uuid, name, remarks) VALUES (?, ?, ?)",
                        (new_uuid, name, remarks))
            conn.commit()
            conn.close()
            restart_xray()
            link = generate_vless_link(new_uuid, remarks)
            self._send_json({"success": True, "uuid": new_uuid, "link": link, "domain_set": bool(CF_DOMAIN)})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_GET(self):
        if self.path == "/health":
            xray_running = subprocess.run(["pgrep", "-f", "xray run"], capture_output=True).returncode == 0
            self._send_json({"status": "ok", "xray": "running" if xray_running else "stopped"})
            return

        if self.path == "/api/me":
            cookies = self._parse_cookies()
            if cookies.get("session_id", "") in sessions:
                self._send_json({"logged_in": True})
            else:
                self._send_json({"logged_in": False}, 401)
            return

        if self.path == "/api/configs":
            cookies = self._parse_cookies()
            if cookies.get("session_id", "") not in sessions:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            conn = get_db()
            rows = conn.execute("SELECT * FROM configs ORDER BY created_at DESC").fetchall()
            conn.close()
            configs = [{
                "id": r["id"], "uuid": r["uuid"], "name": r["name"],
                "remarks": r["remarks"], "enabled": bool(r["enabled"]),
                "vless_link": generate_vless_link(r["uuid"], r["remarks"]),
                "domain_set": bool(CF_DOMAIN)
            } for r in rows]
            self._send_json(configs)
            return

        self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/configs/"):
            config_id = self.path.split("/")[3]
            cookies = self._parse_cookies()
            if cookies.get("session_id", "") not in sessions:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            conn = get_db()
            conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))
            conn.commit()
            conn.close()
            restart_xray()
            self._send_json({"success": True})
            return
        self._send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        if "/toggle" in self.path:
            config_id = self.path.split("/")[3]
            cookies = self._parse_cookies()
            if cookies.get("session_id", "") not in sessions:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            conn = get_db()
            row = conn.execute("SELECT enabled FROM configs WHERE id = ?", (config_id,)).fetchone()
            if row:
                conn.execute("UPDATE configs SET enabled = ? WHERE id = ?",
                           (0 if row["enabled"] else 1, config_id))
                conn.commit()
                restart_xray()
            conn.close()
            self._send_json({"success": True})
            return
        self._send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    print("API Server starting on port 8000...")
    server = HTTPServer(("0.0.0.0", 8000), APIHandler)
    print("API Server running!")
    server.serve_forever()
