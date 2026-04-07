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
import html
import hmac
import http.server
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

DEFAULT_PORT = 8090
COOKIE_NAME = "echobox_auth"
COOKIE_MAX_AGE = 86400 * 7
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def make_token(password: str, secret: str) -> str:
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()[:32]


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
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
<form method="post" action="/">
<label for="password" class="sub">Password</label>
<input id="password" type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
<button type="submit">Enter</button>
<p class="err">WRONG_MSG</p>
</form></div></body></html>"""


REPORT_LIST_HTML = """<!DOCTYPE html>
<html lang="en"><head>
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
    report_dir = Path(".")
    failed_attempts: dict[str, tuple[int, float]] = {}
    _attempts_lock = threading.Lock()

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

    def _client_ip(self) -> str:
        return getattr(self, "client_address", ("unknown", 0))[0]

    def _rate_limit_state(self) -> tuple[int, float]:
        with self._attempts_lock:
            attempts, locked_until = self.failed_attempts.get(self._client_ip(), (0, 0.0))
            if locked_until and time.time() >= locked_until:
                self.failed_attempts.pop(self._client_ip(), None)
                return 0, 0.0
            return attempts, locked_until

    def _is_rate_limited(self) -> bool:
        _, locked_until = self._rate_limit_state()
        return locked_until > time.time()

    def _record_failed_attempt(self) -> None:
        with self._attempts_lock:
            attempts, locked_until = self.failed_attempts.get(self._client_ip(), (0, 0.0))
            if locked_until and time.time() >= locked_until:
                attempts = 0
            attempts += 1
            locked_until = time.time() + LOCKOUT_SECONDS if attempts >= MAX_FAILED_ATTEMPTS else 0.0
            self.failed_attempts[self._client_ip()] = (attempts, locked_until)

    def _clear_failed_attempts(self) -> None:
        with self._attempts_lock:
            self.failed_attempts.pop(self._client_ip(), None)

    def _is_authenticated(self) -> bool:
        cookies = self._parse_cookies()
        token = cookies.get(COOKIE_NAME, "")
        if not token:
            return False
        expected = make_token(self.password, self.hmac_secret)
        return hmac.compare_digest(token, expected)

    def _set_default_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def _send_html(self, content: str, status: int = 200, cache_control: str = "private, no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", cache_control)
        self._set_default_headers()
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _normalize_slug(self, raw_path: str) -> str | None:
        parsed = urllib.parse.urlsplit(raw_path)
        if parsed.query or parsed.fragment:
            return None
        prefix = "/report/"
        if not parsed.path.startswith(prefix):
            return None
        slug = urllib.parse.unquote(parsed.path[len(prefix):]).rstrip("/")
        if not slug or "/" in slug or "\\" in slug:
            return None
        if not SLUG_PATTERN.fullmatch(slug):
            return None
        return slug

    def _send_login(self, wrong: bool = False):
        page = LOGIN_HTML.replace("WRONG_MSG", "Wrong password." if wrong else "")
        self._send_html(page)

    def _send_rate_limited(self) -> None:
        self.send_response(429)
        self.send_header("Retry-After", str(LOCKOUT_SECONDS))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "private, no-store")
        self._set_default_headers()
        self.end_headers()
        self.wfile.write(b"Too many failed login attempts. Try again later.")

    def _send_report_list(self):
        reports = []
        try:
            entries = sorted(self.report_dir.iterdir(), reverse=True)
        except OSError:
            self.send_error(500, "Could not read reports directory")
            return

        for d in entries:
            report_file = d / "report.html"
            if d.is_dir() and report_file.exists():
                try:
                    stat = report_file.stat()
                except OSError:
                    continue
                size_kb = stat.st_size // 1024
                modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                name = urllib.parse.quote(d.name, safe="")
                reports.append(
                    f'<a class="report" href="/report/{name}">'
                    f'<div class="name">{html.escape(d.name)}</div>'
                    f'<div class="meta">{modified} · {size_kb} KB</div></a>'
                )
        if reports:
            report_list = "\n".join(reports)
        else:
            report_list = '<p class="empty">No reports yet. Run echobox enrich + echobox publish first.</p>'

        html = REPORT_LIST_HTML.replace("REPORT_LIST", report_list)
        self._send_html(html)

    def do_GET(self):
        if not self._is_authenticated():
            self._send_login()
            return

        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/" or parsed.path == "":
            self._send_report_list()
            return

        slug = self._normalize_slug(self.path)
        if slug:
            report_file = (self.report_dir / slug / "report.html").resolve()
            try:
                report_file.relative_to(self.report_dir.resolve())
            except ValueError:
                self.send_error(404)
                return
            if report_file.exists() and report_file.is_file():
                try:
                    body = report_file.read_bytes()
                except OSError:
                    self.send_error(500, "Could not read report")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "private, no-store")
                self._set_default_headers()
                self.end_headers()
                self.wfile.write(body)
                return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/" or parsed.query or parsed.fragment:
            self.send_error(404)
            return
        if self._is_rate_limited():
            self._send_rate_limited()
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if content_length > 1024:
            self.send_error(413)
            return
        try:
            body = self.rfile.read(content_length).decode("utf-8")
        except UnicodeDecodeError:
            self.send_error(400, "Invalid form encoding")
            return
        params = urllib.parse.parse_qs(body)
        submitted = params.get("password", [""])[0]
        valid_token = make_token(self.password, self.hmac_secret)

        if hmac.compare_digest(make_token(submitted, self.hmac_secret), valid_token):
            self._clear_failed_attempts()
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={valid_token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={COOKIE_MAX_AGE}",
            )
            self.send_header("Cache-Control", "no-store")
            self._set_default_headers()
            self.end_headers()
        else:
            self._record_failed_attempt()
            self._send_login(wrong=True)


def start_server(report_dir: Path, password: str, port: int = DEFAULT_PORT, tunnel: str = "") -> int:
    secret = secrets.token_hex(32)
    ReportHandler.password = password
    ReportHandler.hmac_secret = secret
    ReportHandler.report_dir = report_dir.resolve()

    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), ReportHandler)
    except OSError as exc:
        print(f"Error: could not start report server on port {port}: {exc}", file=sys.stderr)
        return 1

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
            try:
                tunnel_proc.wait(timeout=5)
            except Exception:
                tunnel_proc.kill()
        if tunnel == "tailscale":
            subprocess.run(["tailscale", "serve", "--remove", "/"], capture_output=True)
        server.server_close()
    return 0


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

    return start_server(report_dir, args.password, args.port, args.tunnel)


if __name__ == "__main__":
    sys.exit(main())
