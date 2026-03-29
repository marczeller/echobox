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
CONFIG = REPO / "config" / "echobox.yaml"


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
    backup = CONFIG.read_text() if CONFIG.exists() else None
    tmp = Path(tempfile.mkdtemp(prefix="echobox-cli-"))
    home = tmp / "home"
    data = tmp / "data"
    bin_dir = tmp / "bin"
    modules_dir = tmp / "modules"
    open_log = tmp / "open.log"
    home.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    try:
        config_payload = {
            "transcript_dir": str(data / "transcripts"),
            "enrichment_dir": str(data / "enrichments"),
            "report_dir": str(data / "reports"),
            "log_dir": str(data / "logs"),
            "mlx_url": "http://127.0.0.1:8090/v1/chat/completions",
            "mlx_model": "demo-model",
            "publish": {"engine": "local", "platform": "local", "password": "super-secret-password"},
        }
        CONFIG.write_text(json.dumps(config_payload))

        write_exec(bin_dir / "ffmpeg", "#!/bin/sh\nexit 0\n")
        write_exec(bin_dir / "curl", "#!/bin/sh\nexit 0\n")
        write_exec(bin_dir / "system_profiler", "#!/bin/sh\necho 'BlackHole 2ch'\n")
        write_exec(bin_dir / "open", f"#!/bin/sh\necho \"$1\" >> {open_log}\n")

        (modules_dir / "yaml.py").write_text(
            "import json\n\ndef safe_load(stream):\n    return json.loads(stream.read())\n"
        )
        (modules_dir / "mlx_whisper.py").write_text(
            "def transcribe(*args, **kwargs):\n    return {'segments': [], 'text': ''}\n"
        )
        (modules_dir / "sounddevice.py").write_text(
            "default = type('Default', (), {'device': [0, 1]})()\n"
            "def query_devices():\n    return [{'name': 'BlackHole 2ch', 'max_input_channels': 2}]\n"
            "class RawInputStream:\n    def __init__(self, *args, **kwargs):\n        pass\n    def start(self):\n        return None\n    def stop(self):\n        return None\n    def close(self):\n        return None\n"
        )
        (modules_dir / "pyannote").mkdir()
        (modules_dir / "pyannote" / "__init__.py").write_text("")
        (modules_dir / "pyannote" / "audio.py").write_text("class Pipeline: pass\n")

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["PYTHONPATH"] = f"{modules_dir}:{env.get('PYTHONPATH', '')}"
        env["ECHOBOX_DATA_DIR"] = str(data)
        env["ECHOBOX_TRANSCRIPT_DIR"] = str(data / "transcripts")
        env["ECHOBOX_ENRICHMENT_DIR"] = str(data / "enrichments")
        env["ECHOBOX_REPORT_DIR"] = str(data / "reports")
        env["ECHOBOX_LOG_DIR"] = str(data / "logs")
        env["ECHOBOX_STATE_DIR"] = str(data)

        transcript_dir = data / "transcripts"
        enrichment_dir = data / "enrichments"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        enrichment_dir.mkdir(parents=True, exist_ok=True)

        base = "2026-03-15_10-00_roadmap-sync"
        (transcript_dir / f"{base}.txt").write_text("Date: 2026-03-15\nDuration: 2:34\n")
        (enrichment_dir / f"{base}-enriched.md").write_text((REPO / "tests" / "fixtures" / f"{base}-enriched.md").read_text())
        sidecar = json.loads((REPO / "tests" / "fixtures" / f"{base}-enriched.json").read_text())
        (enrichment_dir / f"{base}-enriched.json").write_text(json.dumps(sidecar))

        status = subprocess.run(
            [sys.executable, "echobox.py", "status"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(status.returncode == 0, f"status exits successfully: {status.returncode}")
        check("Pipeline: READY" in status.stdout, "status reports READY when dependencies are satisfied")

        shown_config = subprocess.run(
            [sys.executable, "echobox.py", "config"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(shown_config.returncode == 0, f"config exits successfully: {shown_config.returncode}")
        check("(redacted)" in shown_config.stdout, "config redacts sensitive values")
        check("super-secret-password" not in shown_config.stdout, "config does not print publish password")

        listing = subprocess.run(
            [sys.executable, "echobox.py", "list"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(listing.returncode == 0, f"list exits successfully: {listing.returncode}")
        check("Metrics: speakers=2, actions=3, decisions=3, participants=2" in listing.stdout, "list shows sidecar metrics")
        check("Alex Chen and Priya Raman reviewed" in listing.stdout, "list prefers sidecar summary")

        (enrichment_dir / f"{base}-enriched.json").unlink()
        actions = subprocess.run(
            [sys.executable, "echobox.py", "actions"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(actions.returncode == 0, f"actions exits successfully without sidecar: {actions.returncode}")
        check("[Priya Raman]" in actions.stdout, "actions falls back to markdown when sidecar is missing")

        demo = subprocess.run(
            [sys.executable, "echobox.py", "demo"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        report_path = data / "reports" / "2026-03-15-10-00-roadmap-sync-enriched" / "report.html"
        check(demo.returncode == 0, f"demo exits successfully: {demo.returncode}")
        check(report_path.exists(), "demo publishes the fixture report")
        check("Opening demo report" in demo.stdout, "demo announces the final report output")
        check(open_log.exists() and str(report_path) in open_log.read_text(), "demo auto-opens the generated report")
    finally:
        if backup is None:
            CONFIG.unlink(missing_ok=True)
        else:
            CONFIG.write_text(backup)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
