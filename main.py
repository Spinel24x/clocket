import os
import json
import uuid
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            config_id INTEGER DEFAULT NULL,
            FOREIGN KEY(config_id) REFERENCES configs(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

def create_admin():
    conn = get_db()
    admin = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    hashed = hashlib.sha256(USER_PASS.encode()).hexdigest()
    if not admin:
        conn.execute("INSERT INTO users (username, password, is_admin) VALUES ('admin', ?, 1)", (hashed,))
        print("Admin user created")
    else:
        conn.execute("UPDATE users SET password = ? WHERE username = 'admin'", (hashed,))
        print("Admin password updated")
    conn.commit()
    conn.close()

create_admin()

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

# ==================== Xray Config Generator ====================
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

    print("Xray config generated")
    return xray_config

# Generate on import
generate_xray_config()

# ==================== Session ====================
sessions = {}

def get_session(cookies):
    cookie_str = cookies.get("session_id", "")
    return sessions.get(cookie_str)

# ==================== API Handler ====================
class APIHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

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
                from urllib.parse import unquote_plus
                form[unquote_plus(key)] = unquote_plus(value)
        return form

    def _set_cookie(self, key, value):
        self.send_header("Set-Cookie", f"{key}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")

    def log_message(self, format, *args):
        print(f"API: {format % args}")

    def do_POST(self):
        if self.path == "/api/login":
            form = self._parse_form()
            username = form.get("username", "")
            password = form.get("password", "")

            conn = get_db()
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()

            hashed = hashlib.sha256(password.encode()).hexdigest()

            if not user or user["password"] != hashed:
                self._send_json({"detail": "Invalid username or password"}, 401)
                return

            session_id = secrets.token_hex(32)
            sessions[session_id] = {
                "id": user["id"], "username": user["username"],
                "is_admin": bool(user["is_admin"]), "config_id": user["config_id"]
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._set_cookie("session_id", session_id)
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "is_admin": bool(user["is_admin"])}).encode())
            return

        if self.path == "/api/logout":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._set_cookie("session_id", "")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        if self.path == "/api/configs":
            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
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

            generate_xray_config()

            self._send_json({
                "success": True,
                "id": config_id,
                "uuid": new_uuid,
                "link": generate_vless_link(new_uuid, remarks),
                "domain_set": bool(CF_DOMAIN)
            })
            return

        if self.path.startswith("/api/users"):
            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            form = self._parse_form()
            username = form.get("username", "")
            password = form.get("password", "")

            hashed = hashlib.sha256(password.encode()).hexdigest()
            conn = get_db()
            try:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                            (username, hashed))
                conn.commit()
                self._send_json({"success": True})
            except sqlite3.IntegrityError:
                self._send_json({"detail": "Username exists"}, 400)
            finally:
                conn.close()
            return

        self._send_json({"error": "Not found"}, 404)

    def do_GET(self):
        cookies = self._parse_cookies()
        session = get_session(cookies)

        if self.path == "/api/me":
            if not session:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            self._send_json({
                "username": session["username"],
                "is_admin": session["is_admin"],
                "config_id": session["config_id"]
            })
            return

        if self.path == "/api/configs":
            if not session or not session.get("is_admin"):
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

        if self.path == "/api/users":
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            conn = get_db()
            rows = conn.execute("""
                SELECT u.id, u.username, u.is_admin, u.config_id, c.uuid as cuuid, c.name as cname
                FROM users u LEFT JOIN configs c ON u.config_id = c.id ORDER BY u.id DESC
            """).fetchall()
            conn.close()

            users = []
            for r in rows:
                users.append({
                    "id": r["id"], "username": r["username"],
                    "is_admin": bool(r["is_admin"]), "config_id": r["config_id"],
                    "config_uuid": r["cuuid"], "config_name": r["cname"]
                })

            self._send_json(users)
            return

        if self.path == "/api/my-config":
            if not session or not session.get("config_id"):
                self._send_json({"has_config": False})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM configs WHERE id = ?", (session["config_id"],)).fetchone()
            conn.close()

            if not row:
                self._send_json({"has_config": False})
                return

            self._send_json({
                "has_config": True, "id": row["id"], "uuid": row["uuid"],
                "name": row["name"], "remarks": row["remarks"],
                "vless_link": generate_vless_link(row["uuid"], row["remarks"]),
                "domain_set": bool(CF_DOMAIN)
            })
            return

        if self.path == "/health":
            self._send_json({"status": "ok", "cf_domain": CF_DOMAIN or "not set"})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        if self.path.startswith("/api/configs/"):
            parts = self.path.split("/")
            if len(parts) >= 4:
                config_id = parts[3]
            else:
                self._send_json({"error": "Invalid ID"}, 400)
                return

            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            form = self._parse_form()
            conn = get_db()
            conn.execute("UPDATE configs SET name=?, remarks=?, traffic_limit_gb=? WHERE id=?",
                        (form.get("name", ""), form.get("remarks", ""),
                         form.get("traffic_limit_gb", 0), config_id))
            conn.commit()
            conn.close()
            generate_xray_config()
            self._send_json({"success": True})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/configs/"):
            parts = self.path.split("/")
            if len(parts) >= 4:
                config_id = parts[3]
            else:
                self._send_json({"error": "Invalid ID"}, 400)
                return

            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            conn = get_db()
            conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))
            conn.execute("UPDATE users SET config_id = NULL WHERE config_id = ?", (config_id,))
            conn.commit()
            conn.close()
            generate_xray_config()
            self._send_json({"success": True})
            return

        if self.path.startswith("/api/users/"):
            parts = self.path.split("/")
            if len(parts) >= 4:
                user_id = parts[3]
            else:
                self._send_json({"error": "Invalid ID"}, 400)
                return

            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            conn = get_db()
            conn.execute("DELETE FROM users WHERE id = ? AND username != 'admin'", (user_id,))
            conn.commit()
            conn.close()
            self._send_json({"success": True})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        if self.path.startswith("/api/configs/") and "/toggle" in self.path:
            config_id = self.path.split("/")[3]
            cookies = self._parse_cookies()
            session = get_session(cookies)
            if not session or not session.get("is_admin"):
                self._send_json({"error": "Unauthorized"}, 401)
                return

            conn = get_db()
            row = conn.execute("SELECT enabled FROM configs WHERE id = ?", (config_id,)).fetchone()
            if row:
                conn.execute("UPDATE configs SET enabled = ? WHERE id = ?",
                           (0 if row["enabled"] else 1, config_id))
                conn.commit()
                generate_xray_config()
            conn.close()
            self._send_json({"success": True})
            return

        self._send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    print("API Server starting on port 8000")
    server = HTTPServer(("0.0.0.0", 8000), APIHandler)
    server.serve_forever()
