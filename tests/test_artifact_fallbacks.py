#!/usr/bin/env python3
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


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-artifacts-"))
    data = tmp / "data"
    transcript_dir = data / "transcripts"
    enrichment_dir = data / "enrichments"
    report_dir = data / "reports"
    log_dir = data / "logs"
    for path in [transcript_dir, enrichment_dir, report_dir, log_dir]:
        path.mkdir(parents=True, exist_ok=True)

    transcript_id = "2026-03-15_10-00_failed-enrichment"
    transcript = transcript_dir / f"{transcript_id}.txt"
    transcript.write_text("Date: 2026-03-15\nCall: Failed enrichment demo\n")

    env = os.environ.copy()
    env["ECHOBOX_DATA_DIR"] = str(data)
    env["ECHOBOX_TRANSCRIPT_DIR"] = str(transcript_dir)
    env["ECHOBOX_ENRICHMENT_DIR"] = str(enrichment_dir)
    env["ECHOBOX_REPORT_DIR"] = str(report_dir)
    env["ECHOBOX_LOG_DIR"] = str(log_dir)
    env["ECHOBOX_STATE_DIR"] = str(data)
    env["ECHOBOX_DISABLE_TEE_LOGGING"] = "true"
    env["ECHOBOX_MLX_URL"] = "http://127.0.0.1:9/v1/chat/completions"

    try:
        orchestrator = subprocess.run(
            ["bash", "pipeline/orchestrator.sh", transcript_id],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_output = enrichment_dir / f"{transcript_id}-raw.md"
        enriched_output = enrichment_dir / f"{transcript_id}-enriched.md"
        check(orchestrator.returncode == 0, f"orchestrator exits successfully on raw fallback: {orchestrator.returncode}")
        check(raw_output.exists(), "raw fallback writes *-raw.md")
        check(not enriched_output.exists(), "raw fallback does not leave *-enriched.md behind")

        raw_listing = subprocess.run(
            [sys.executable, "pipeline/list_calls.py", str(transcript_dir), str(enrichment_dir), str(report_dir)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check("raw (not enriched)" in raw_listing.stdout, "list marks raw fallback distinctly")

        missing_publish = subprocess.run(
            ["bash", "pipeline/publish.sh", str(raw_output)],
            cwd=REPO,
            env={**env, "ECHOBOX_TRANSCRIPT_DIR": str(tmp / "missing-transcripts")},
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(missing_publish.returncode != 0, "publish fails when matching transcript is missing")
        check("matching transcript not found" in missing_publish.stdout, "publish emits a clear missing transcript error")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
