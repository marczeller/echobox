#!/usr/bin/env python3
"""Smoke tests for path expansion in echobox.py."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
CONFIG = REPO / "config" / "echobox.yaml"


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    backup = None
    if CONFIG.exists():
        backup = CONFIG.read_text()

    temp_home = Path(tempfile.mkdtemp(prefix="echobox-home-"))
    repo_tilde = REPO / "~"
    if repo_tilde.exists():
        shutil.rmtree(repo_tilde)

    try:
        config_text = """transcript_dir: ~/echobox-data/transcripts
enrichment_dir: ~/echobox-data/enrichments
report_dir: ~/echobox-data/reports
log_dir: ~/echobox-data/logs
"""
        CONFIG.write_text(config_text)

        env = os.environ.copy()
        env["HOME"] = str(temp_home)

        result = subprocess.run(
            [sys.executable, "echobox.py", "version"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        check(result.returncode == 0, f"echobox version exits successfully: {result.returncode}")
        check((temp_home / "echobox-data" / "transcripts").is_dir(), "tilde transcript path expands to HOME")
        check((temp_home / "echobox-data" / "reports").is_dir(), "tilde report path expands to HOME")
        check(not repo_tilde.exists(), "repo-local literal ~ directory is not created")
    finally:
        if backup is None:
            CONFIG.unlink(missing_ok=True)
        else:
            CONFIG.write_text(backup)
        shutil.rmtree(temp_home, ignore_errors=True)
        if repo_tilde.exists():
            shutil.rmtree(repo_tilde, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
