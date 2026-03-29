#!/usr/bin/env python3
"""Password-gated HTTP server for Echobox reports.

Three publish modes:
  local     — serve on a local port (use tunnel for remote access)
  tailscale — serve locally + tailscale serve for public URL
  vercel    — deploy to Vercel CDN (report leaves machine)

All modes are password-gated with cookie-based auth.
"""
from __future__ import annotations

import hashlib
import hmac
import http.server
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

DEFAULT_PORT = 8090
COOKIE_NAME = "echobox_auth"
COOKIE_MAX_AGE = 86400 * 7


def make_token(password: str, secret: str) -> str:
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()[:32]


LOGIN_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Echobox Reports</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#10131a;color:#f3efe8;height:100dvh;display:flex;align-items:center;justify-content:center}
.gate{text-align:center;max-width:360px;padding:0 24px}
h1{font-size:42px;font-weight:800;color:#ff7b54;letter-spacing:-2px;margin-bottom:4px}
.sub{font-size:12px;color:#a9a39b;letter-spacing:3px;text-transform:uppercase;margin-bottom:32px}
form{display:flex;flex-direction:column;gap:12px}
input[type=password]{font-size:16px;padding:14px 18px;background:#1a1e2a;border:1px solid rgba(255,123,84,0.15);border-radius:8px;color:#f3efe8;outline:0}
input:focus{border-color:#ff7b54}
button{font-size:15px;font-weight:700;padding:14px;background:#ff7b54;color:#10131a;border:0;border-radius:8px;cursor:pointer}
.err{font-size:12px;color:#ef5350;margin-top:8px;min-height:18px}
</style></head><body>
<div class="gate">
<h1>Echobox</h1>
<p class="sub">Call Reports</p>
<form method=POST action="/">
<input type=password name=password placeholder=Password autofocus>
<button>Enter</button>
<p class="err">WRONG_MSG</p>
</form></div></body></html>"""


REPORT_LIST_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Echobox Reports</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#10131a;color:#f3efe8;padding:40px 24px}
.container{max-width:700px;margin:0 auto}
h1{font-size:28px;font-weight:800;color:#ff7b54;letter-spacing:-1px;margin-bottom:24px}
a{color:#f3efe8;text-decoration:none}
.report{display:block;padding:16px;margin-bottom:12px;background:#1a1e2a;border:1px solid rgba(255,123,84,0.1);border-radius:10px;transition:border-color 0.15s}
.report:hover{border-color:#ff7b54}
.report .name{font-weight:700;font-size:15px}
.report .meta{font-size:12px;color:#a9a39b;margin-top:4px}
.empty{color:#a9a39b;font-size:14px}
</style></head><body>
<div class="container">
<h1>Echobox Reports</h1>
REPORT_LIST
</div></body></html>"""


class ReportHandler(http.server.BaseHTTPRequestHandler):
    password = ""
    hmac_secret = ""
    valid_token = ""
    report_dir = Path(".")

    def log_message(self, format, *args):
        pass

    def _parse_cookies(self) -> dict:
        cookies = {}
        header = self.headers.get("Cookie", "")
        for part in header.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    def _is_authenticated(self) -> bool:
        cookies = self._parse_cookies()
        token = cookies.get(COOKIE_NAME, "")
        return hmac.compare_digest(token, self.valid_token)

    def _send_login(self, wrong: bool = False):
        html = LOGIN_HTML.replace("WRONG_MSG", "Wrong password." if wrong else "")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_report_list(self):
        reports = []
        for d in sorted(self.report_dir.iterdir(), reverse=True):
            report_file = d / "report.html"
            if d.is_dir() and report_file.exists():
                stat = report_file.stat()
                size_kb = stat.st_size // 1024
                modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                name = d.name
                reports.append(
                    f'<a class="report" href="/report/{name}">'
                    f'<div class="name">{name}</div>'
                    f'<div class="meta">{modified} · {size_kb} KB</div></a>'
                )
        if reports:
            report_list = "\n".join(reports)
        else:
            report_list = '<p class="empty">No reports yet. Run echobox enrich + echobox publish first.</p>'

        html = REPORT_LIST_HTML.replace("REPORT_LIST", report_list)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "private, no-cache")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        if not self._is_authenticated():
            self._send_login()
            return

        if self.path == "/" or self.path == "":
            self._send_report_list()
            return

        if self.path.startswith("/report/"):
            slug = self.path[len("/report/"):].rstrip("/")
            slug = slug.replace("..", "").replace("/", "")
            report_file = self.report_dir / slug / "report.html"
            if report_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "private, no-cache")
                self.end_headers()
                self.wfile.write(report_file.read_bytes())
                return

        self.send_error(404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 1024:
            self.send_error(413)
            return
        body = self.rfile.read(content_length).decode()
        params = urllib.parse.parse_qs(body)
        submitted = params.get("password", [""])[0]

        if hmac.compare_digest(make_token(submitted, self.hmac_secret), self.valid_token):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self.valid_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_MAX_AGE}",
            )
            self.end_headers()
        else:
            self._send_login(wrong=True)


def start_server(report_dir: Path, password: str, port: int = DEFAULT_PORT, tunnel: str = "") -> None:
    secret = hashlib.sha256(f"echobox:{password}".encode()).hexdigest()
    token = make_token(password, secret)

    ReportHandler.password = password
    ReportHandler.hmac_secret = secret
    ReportHandler.valid_token = token
    ReportHandler.report_dir = report_dir

    server = http.server.HTTPServer(("0.0.0.0", port), ReportHandler)

    import socket
    hostname = socket.gethostname()
    print("Echobox Report Server")
    print(f"  Reports:  {report_dir}")
    print(f"  Local:    http://localhost:{port}")
    print(f"  Network:  http://{hostname}:{port}")
    print(f"  Password: {'*' * len(password)} (set in config)")
    print(flush=True)

    tunnel_proc = None
    if tunnel == "tailscale":
        print("Starting Tailscale serve...")
        tailscale_url = _start_tailscale(port)
        if tailscale_url:
            print(f"  Public:   {tailscale_url}")
        else:
            print("  Tailscale serve failed — serving locally only")
    elif tunnel == "bore":
        print("Starting bore tunnel...")
        tunnel_proc = _start_bore(port)
    elif tunnel:
        print(f"Unknown tunnel: {tunnel}. Serving locally only.")

    print()
    print("Press Ctrl+C to stop.")
    print(flush=True)
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()
        if tunnel == "tailscale":
            subprocess.run(["tailscale", "serve", "--remove", "/"], capture_output=True)
        server.server_close()


def _start_tailscale(port: int) -> str:
    try:
        subprocess.run(
            ["tailscale", "serve", "--bg", f"http://127.0.0.1:{port}"],
            capture_output=True, text=True, timeout=10,
        )
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            import json
            status = json.loads(result.stdout)
            dns_name = status.get("Self", {}).get("DNSName", "").rstrip(".")
            if dns_name:
                return f"https://{dns_name}"
    except Exception:
        pass
    return ""


def _start_bore(port: int) -> subprocess.Popen | None:
    if not shutil.which("bore"):
        print("  bore not found. Install: brew install bore-cli")
        return None
    try:
        proc = subprocess.Popen(
            ["bore", "local", str(port), "--to", "bore.pub"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(2)
        if proc.poll() is not None:
            print("  bore failed to start")
            return None
        print(f"  Tunnel active via bore.pub")
        return proc
    except Exception as e:
        print(f"  bore error: {e}")
        return None


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Serve Echobox reports with password gate")
    parser.add_argument("report_dir", help="Path to reports directory")
    parser.add_argument("--password", required=True, help="Access password")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--tunnel", choices=["tailscale", "bore", ""], default="", help="Tunnel for remote access")
    args = parser.parse_args()

    report_dir = Path(args.report_dir).expanduser()
    if not report_dir.exists():
        print(f"Error: report directory not found: {report_dir}", file=sys.stderr)
        return 1

    start_server(report_dir, args.password, args.port, args.tunnel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
