#!/usr/bin/env python3
from __future__ import annotations

"""Smoke tests for the password-gated report server."""

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.serve import COOKIE_NAME
from pipeline.serve import LOGIN_HTML
from pipeline.serve import ReportHandler
from pipeline.serve import make_token

PASS = 0
FAIL = 0


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def build_handler(password: str, secret: str, cookie: str = "") -> ReportHandler:
    handler = object.__new__(ReportHandler)
    handler.headers = {"Cookie": cookie}
    handler.client_address = ("127.0.0.1", 12345)
    handler.wfile = io.BytesIO()
    handler.status_codes = []
    handler.header_items = []
    handler.end_headers = lambda: None
    handler.send_response = lambda code: handler.status_codes.append(code)
    handler.send_header = lambda key, value: handler.header_items.append((key, value))
    ReportHandler.password = password
    ReportHandler.hmac_secret = secret
    return handler


def main():
    password = "correct horse battery staple"
    secret = "test-secret"
    token = make_token(password, secret)

    anonymous = build_handler(password, secret)
    check(not anonymous._is_authenticated(), "missing cookie is rejected")

    authenticated = build_handler(password, secret, f"{COOKIE_NAME}={token}")
    check(authenticated._is_authenticated(), "valid auth cookie is accepted")

    wrong_cookie = build_handler(password, secret, f"{COOKIE_NAME}=wrong")
    check(not wrong_cookie._is_authenticated(), "wrong auth cookie is rejected")

    limiter = build_handler(password, secret)
    ReportHandler.failed_attempts = {}
    for _ in range(5):
        limiter._record_failed_attempt()
    check(limiter._is_rate_limited(), "repeated failures trigger a temporary lockout")
    limiter._clear_failed_attempts()
    check(not limiter._is_rate_limited(), "successful auth path can clear rate limiting")

    slug_handler = build_handler(password, secret)
    check(slug_handler._normalize_slug("/report/demo-slug") == "demo-slug", "simple report slug is accepted")
    check(slug_handler._normalize_slug("/report/demo%20slug") is None, "spaces are rejected in report slug")
    check(slug_handler._normalize_slug("/report/../secret") is None, "dot-dot traversal is rejected")
    check(slug_handler._normalize_slug("/report/%2e%2e%2fsecret") is None, "encoded traversal is rejected")
    check(slug_handler._normalize_slug("/report/demo?download=1") is None, "query-string variant is rejected")

    login_handler = build_handler(password, secret)
    login_handler._send_login()
    login_page = login_handler.wfile.getvalue().decode("utf-8")
    check(login_handler.status_codes == [200], "login page returns HTTP 200")
    check("<!DOCTYPE html>" in login_page and '<html lang="en">' in login_page, "login page is well-formed HTML")
    check('method="post"' in login_page and 'autocomplete="current-password"' in login_page, "login form posts password field")
    check("Wrong password." not in login_page, "fresh login page does not show an error")

    wrong_login_handler = build_handler(password, secret)
    wrong_login_handler._send_login(wrong=True)
    wrong_login_page = wrong_login_handler.wfile.getvalue().decode("utf-8")
    header_map = dict(wrong_login_handler.header_items)
    check("Wrong password." in wrong_login_page, "failed login renders the error message")
    check(header_map.get("Cache-Control") == "private, no-store", "login page disables caching")
    check(header_map.get("X-Frame-Options") == "DENY", "login page blocks framing")
    check("form-action 'self'" in header_map.get("Content-Security-Policy", ""), "login page sets a restrictive CSP")

    report_dir = Path(tempfile.mkdtemp(prefix="echobox-serve-test-"))
    try:
        report_handler = build_handler(password, secret)
        report_handler.report_dir = report_dir
        check(report_handler._send_report_list() is None, "empty report list renders without crashing")
    finally:
        report_dir.rmdir()

    check('method="post"' in LOGIN_HTML, "login HTML template uses an explicit POST method")

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
