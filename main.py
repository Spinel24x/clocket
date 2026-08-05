import os, json, uuid, sqlite3, subprocess, secrets, hashlib, time, signal, sys, logging, threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote_plus, urlparse

# ----------------------------- Config -----------------------------
class Config:
    CF_DOMAIN = os.getenv("CF_DOMAIN", "")
    USER_PASS = os.getenv("USER_PASS", "admin123")
    DB_PATH = "/app/data/panel.db"
    CONFIG_PATH = "/app/configs/xray.json"
    XRAY_BINARY = "/usr/local/bin/xray"
    XRAY_PORT = 10000
    API_PORT = 8000

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
logger = logging.getLogger("VLESS")
logger.info(f"CF_DOMAIN: {Config.CF_DOMAIN or 'NOT SET'}")

# ----------------------------- Database -----------------------------
class Database:
    def __init__(self):
        Path(Config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(Config.DB_PATH)
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
        conn = sqlite3.connect(Config.DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM configs WHERE enabled = 1 ORDER BY created_at DESC").fetchall()
        conn.close()
        return rows

    def add(self, uid, name, remarks):
        conn = sqlite3.connect(Config.DB_PATH)
        conn.execute("INSERT INTO configs (uuid, name, remarks) VALUES (?, ?, ?)", (uid, name, remarks))
        conn.commit()
        conn.close()

    def delete(self, cid):
        conn = sqlite3.connect(Config.DB_PATH)
        conn.execute("DELETE FROM configs WHERE id = ?", (cid,))
        conn.commit()
        conn.close()

    def toggle(self, cid):
        conn = sqlite3.connect(Config.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT enabled FROM configs WHERE id = ?", (cid,)).fetchone()
        if row:
            conn.execute("UPDATE configs SET enabled = ? WHERE id = ?", (0 if row["enabled"] else 1, cid))
            conn.commit()
        conn.close()

db = Database()

# ----------------------------- VLESS Link -----------------------------
def make_vless(uid, remarks=""):
    if not Config.CF_DOMAIN:
        return ""
    r = remarks or "VLESS"
    return (f"vless://{uid}@{Config.CF_DOMAIN}:443?"
            f"encryption=none&security=tls&sni={Config.CF_DOMAIN}"
            f"&fp=chrome&type=ws&host={Config.CF_DOMAIN}"
            f"&path=%2Fws#{r}")

# ----------------------------- Xray Manager -----------------------------
xray_process = None

def build_xray_config():
    rows = db.get_all()
    clients = [{"id": r["uuid"]} for r in rows]
    if not clients:
        clients = [{"id": str(uuid.uuid4())}]

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": Config.XRAY_PORT,
            "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/ws"}}
        }],
        "outbounds": [{"protocol": "freedom", "settings": {}}]
    }

    Path(Config.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(Config.CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    logger.info(f"Xray config: {len(clients)} clients")

def start_xray():
    global xray_process
    if xray_process and xray_process.poll() is None:
        xray_process.terminate()
        try:
            xray_process.wait(3)
        except:
            xray_process.kill()
    build_xray_config()
    xray_process = subprocess.Popen(
        [Config.XRAY_BINARY, "run", "-config", Config.CONFIG_PATH],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    def reader():
        for line in xray_process.stdout:
            logger.info(f"XRAY: {line.decode().strip()}")
    threading.Thread(target=reader, daemon=True).start()
    logger.info("Xray started")

def restart_xray():
    start_xray()

def is_xray_running():
    return xray_process is not None and xray_process.poll() is None

start_xray()

# ----------------------------- Sessions -----------------------------
sessions = {}

# ----------------------------- HTTP Handler -----------------------------
class Handler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cookies(self):
        c = {}
        for item in self.headers.get("Cookie", "").split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                c[k] = v
        return c

    def _form(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            f = {}
            for item in body.split("&"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    f[unquote_plus(k)] = unquote_plus(v)
            return f
        except:
            return {}

    def _auth(self):
        return self._cookies().get("session_id", "") in sessions

    def log_message(self, fmt, *args):
        logger.info(f"{self.path}: {fmt % args}")

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            self._json({"status": "ok", "xray": "running" if is_xray_running() else "stopped"})
        elif p == "/api/me":
            self._json({"logged_in": self._auth()}, 200 if self._auth() else 401)
        elif p == "/api/configs":
            if not self._auth():
                return self._json({"error": "Unauthorized"}, 401)
            rows = db.get_all()
            self._json([{"id": r["id"], "uuid": r["uuid"], "name": r["name"],
                         "remarks": r["remarks"], "enabled": bool(r["enabled"]),
                         "vless_link": make_vless(r["uuid"], r["remarks"]),
                         "domain_set": bool(Config.CF_DOMAIN)} for r in rows])
        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/login":
            if self._form().get("password", "") != Config.USER_PASS:
                return self._json({"detail": "Wrong password"}, 401)
            sid = secrets.token_hex(32)
            sessions[sid] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"session_id={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            logger.info("Login OK")
        elif p == "/api/logout":
            sessions.pop(self._cookies().get("session_id", ""), None)
            self._json({"success": True})
        elif p == "/api/configs":
            if not self._auth():
                return self._json({"error": "Unauthorized"}, 401)
            f = self._form()
            uid = str(uuid.uuid4())
            db.add(uid, f.get("name", "Unnamed"), f.get("remarks", ""))
            restart_xray()
            self._json({"success": True, "uuid": uid, "link": make_vless(uid, f.get("remarks", "")),
                        "domain_set": bool(Config.CF_DOMAIN)})
            logger.info(f"Config created: {uid}")
        else:
            self._json({"error": "Not found"}, 404)

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/configs/") and self._auth():
            try:
                db.delete(int(p.split("/")[3]))
                restart_xray()
                self._json({"success": True})
            except:
                self._json({"error": "Invalid ID"}, 400)
        else:
            self._json({"error": "Not found"}, 404)

    def do_PATCH(self):
        p = urlparse(self.path).path
        if "/toggle" in p and self._auth():
            try:
                db.toggle(int(p.split("/")[3]))
                restart_xray()
                self._json({"success": True})
            except:
                self._json({"error": "Invalid ID"}, 400)
        else:
            self._json({"error": "Not found"}, 404)

# ----------------------------- Main -----------------------------
def main():
    logger.info(f"API on 0.0.0.0:{Config.API_PORT}")
    server = HTTPServer(("0.0.0.0", Config.API_PORT), Handler)
    def shutdown(sig, frame):
        server.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logger.info("Ready!")
    server.serve_forever()

if __name__ == "__main__":
    main()
