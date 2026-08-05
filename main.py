import os
import json
import uuid
import sqlite3
import subprocess
import secrets
import hashlib
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
print(f"Xray port: {XRAY_PORT}")

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
            traffic_limit_gb REAL DEFAULT 0,
            traffic_used_gb REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            expire_at TEXT DEFAULT NULL
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
xray_process = None

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

    print(f"Xray config updated with {len(clients)} clients")
    return xray_config

def restart_xray():
    global xray_process
    generate_xray_config()
    try:
        subprocess.run(["pkill", "-f", "xray run"], check=False)
        import time
        time.sleep(1)
    except:
        pass
    xray_process = subprocess.Popen(
        ["/usr/local/bin/xray", "run", "-config", CONFIG_PATH],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    import threading
    def log_xray():
        if xray_process and xray_process.stdout:
            for line in xray_process.stdout:
                line_str = line.decode().strip()
                if line_str:
                    print(f"XRAY: {line_str}")
    threading.Thread(target=log_xray, daemon=True).start()
    print("Xray restarted")

restart_xray()

# ==================== Session ====================
sessions = {}

def is_logged_in(cookies):
    session_id = cookies.get("session_id", "")
    return session_id in sessions

# ==================== API Handler ====================
class APIHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

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

    def _set_cookie(self, key, value):
        self.send_header("Set-Cookie", f"{key}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")

    def log_message(self, format, *args):
        print(f"API [{self.path}]: {format % args}")

    def do_POST(self):
        # لاگین فقط با پسورد
        if self.path == "/api/login":
            form = self._parse_form()
            password = form.get("password", "")

            if password != USER_PASS:
                print(f"Login failed: wrong password")
                self._send_json({"detail": "Wrong password"}, 401)
                return

            session_id = secrets.token_hex(32)
            sessions[session_id] = True

            print("Login successful")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._set_cookie("session_id", session_id)
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        # لاگ‌اوت
        if self.path == "/api/logout":
            cookies = self._parse_cookies()
            session_id = cookies.get("session_id", "")
            if session_id in sessions:
                del sessions[session_id]
            self._send_json({"success": True})
            return

        # ساخت کانفیگ جدید
        if self.path == "/api/configs":
            cookies = self._parse_cookies()
            if not is_logged_in(cookies):
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
            config_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()

            restart_xray()
            link = generate_vless_link(new_uuid, remarks)
            print(f"Config created: {new_uuid}")

            self._send_json({
                "success": True,
                "id": config_id,
                "uuid": new_uuid,
                "link": link,
                "domain_set": bool(CF_DOMAIN)
            })
            return

        self._send_json({"error": "Not found"}, 404)

    def do_GET(self):
        cookies = self._parse_cookies()

        # چک لاگین
        if self.path == "/api/me":
            if is_logged_in(cookies):
                self._send_json({"logged_in": True})
            else:
                self._send_json({"logged_in": False}, 401)
            return

        # لیست کانفیگ‌ها
        if self.path == "/api/configs":
            if not is_logged_in(cookies):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            conn = get_db()
            rows = conn.execute("SELECT * FROM configs ORDER BY created_at DESC").fetchall()
            conn.close()

            configs = []
            for r in rows:
                configs.append({
                    "id": r["id"], "uuid": r["uuid"], "name": r["name"],
                    "remarks": r["remarks"], "enabled": bool(r["enabled"]),
                    "traffic_limit_gb": r["traffic_limit_gb"],
                    "traffic_used_gb": r["traffic_used_gb"],
                    "created_at": r["created_at"], "expire_at": r["expire_at"],
                    "vless_link": generate_vless_link(r["uuid"], r["remarks"]),
                    "domain_set": bool(CF_DOMAIN)
                })

            self._send_json(configs)
            return

        # health check
        if self.path == "/health":
            xray_status = "running" if subprocess.run(["pgrep", "-f", "xray run"], capture_output=True).returncode == 0 else "stopped"
            self._send_json({
                "status": "ok",
                "xray": xray_status,
                "cf_domain": CF_DOMAIN or "not set"
            })
            return

        self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/configs/"):
            config_id = self.path.split("/")[3]
            cookies = self._parse_cookies()
            if not is_logged_in(cookies):
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
            if not is_logged_in(cookies):
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
    print("API Server starting on port 8000")
    server = HTTPServer(("0.0.0.0", 8000), APIHandler)
    server.serve_forever()
