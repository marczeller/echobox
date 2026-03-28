#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def write_exec(path: Path, content: str):
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-smart-setup-"))
    home = tmp / "home"
    bin_dir = tmp / "bin"
    project_dir = home / "Code" / "alpha"
    notes_dir = home / "Notes"
    messages_dir = home / "Library" / "Messages"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)
    messages_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir()

    try:
        write_exec(bin_dir / "system_profiler", "#!/bin/sh\necho 'BlackHole 2ch'\n")
        write_exec(bin_dir / "gcalcli", "#!/bin/sh\necho '{\"items\": []}'\n")
        write_exec(bin_dir / "ffmpeg", "#!/bin/sh\nexit 0\n")
        write_exec(bin_dir / "trnscrb", "#!/bin/sh\nexit 0\n")

        chat_db = messages_dir / "chat.db"
        subprocess.run(["sqlite3", str(chat_db), "CREATE TABLE message (date INTEGER, text TEXT, handle_id INTEGER); CREATE TABLE handle (ROWID INTEGER, id TEXT);"], check=True)

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["PROJECT_DIR"] = str(project_dir)

        result = subprocess.run(
            [sys.executable, "pipeline/smart_setup.py", "--format", "json"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(result.returncode == 0, f"smart_setup json exits successfully: {result.returncode}")
        payload = json.loads(result.stdout)
        check(payload["probes"]["calendar_probe"]["tool"] == "gcalcli", "detects gcalcli as calendar tool")
        check(payload["probes"]["messages"]["exists"], "detects Messages chat.db")
        check(payload["recommendations"]["context_sources"]["messages"]["type"] == "sqlite", "recommends sqlite messages source")
        check("documents" in payload["recommendations"]["context_sources"], "recommends documents source when project dir exists")

        markdown = subprocess.run(
            [sys.executable, "pipeline/smart_setup.py"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(markdown.returncode == 0, f"smart_setup markdown exits successfully: {markdown.returncode}")
        check("# Echobox Smart Setup Report" in markdown.stdout, "renders markdown report")
        check("## Suggested Config" in markdown.stdout, "markdown includes suggested config section")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
