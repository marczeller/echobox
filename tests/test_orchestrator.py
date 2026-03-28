#!/usr/bin/env python3
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PASS = FAIL = 0
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
    path.chmod(0o755)

def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-orch-"))
    data = tmp / "local-data"
    remote = tmp / "remote"
    bin_dir = tmp / "bin"
    transcript_dir = data / "transcripts"
    enrichment_dir = data / "enrichments"
    report_dir = data / "reports"
    log_dir = data / "logs"
    for path in [transcript_dir, enrichment_dir, report_dir, log_dir, remote, bin_dir]:
        path.mkdir(parents=True, exist_ok=True)

    transcript_id = "2026-03-15_10-00_remote-sync"
    (transcript_dir / f"{transcript_id}.txt").write_text("raw transcript from laptop\n")

    write_exec(bin_dir / "curl", "#!/bin/sh\nexit 22\n")
    write_exec(bin_dir / "rsync", """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
remote = Path(os.environ["ECHOBOX_TEST_REMOTE_ROOT"])
args = [a for a in sys.argv[1:] if not a.startswith("-")]
src_raw, dst_raw = args
def map_path(v):
    if ":" in v:
        _, p = v.split(":", 1)
        return remote / (p[2:] if p.startswith("~/") else p)
    return Path(v)
src, dst = map_path(src_raw), map_path(dst_raw)
if src_raw.endswith("/"):
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        shutil.copytree(child, target, dirs_exist_ok=True) if child.is_dir() else shutil.copy2(child, target)
else:
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
""")
    write_exec(bin_dir / "ssh", """#!/usr/bin/env python3
import os, re, sys
from pathlib import Path
remote = Path(os.environ["ECHOBOX_TEST_REMOTE_ROOT"])
args = sys.argv[1:]
while args and args[0].startswith("-"):
    flag = args.pop(0)
    if flag in {"-o", "-p"} and args: args.pop(0)
args.pop(0)
cmd = " ".join(args)
m = re.search(r"transcripts/([^ ]+) -o ~/echobox-data/enrichments/([^ ]+)", cmd)
if not m: raise SystemExit(1)
transcript = remote / "echobox-data" / "transcripts" / m.group(1)
enrichment = remote / "echobox-data" / "enrichments" / m.group(2)
enrichment.parent.mkdir(parents=True, exist_ok=True)
enrichment.write_text("# Remote Enrichment\\n\\n" + transcript.read_text())
enrichment.with_suffix(".json").write_text('{"speakers":[{"label":"SPEAKER_00"}],"action_items":[{"owner":"Alex","task":"Follow up","deadline":""}],"decisions":["Ship it"],"participants":[{"name":"Alex"}],"follow_ups":["Confirm rollout"],"meeting_type":"team_sync"}')
""")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ECHOBOX_TEST_REMOTE_ROOT"] = str(remote)
    env["ECHOBOX_WORKSTATION"] = "fake-workstation"
    env["ECHOBOX_DATA_DIR"] = str(data)
    env["ECHOBOX_TRANSCRIPT_DIR"] = str(transcript_dir)
    env["ECHOBOX_ENRICHMENT_DIR"] = str(enrichment_dir)
    env["ECHOBOX_REPORT_DIR"] = str(report_dir)
    env["ECHOBOX_LOG_DIR"] = str(log_dir)
    env["ECHOBOX_STATE_DIR"] = str(data)
    env["ECHOBOX_DISABLE_TEE_LOGGING"] = "true"
    env["HOME"] = str(tmp / "home")

    try:
        result = subprocess.run(
            ["bash", "pipeline/orchestrator.sh", transcript_id],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=30,
        )
        output = enrichment_dir / f"{transcript_id}-enriched.md"
        check(result.returncode == 0, f"orchestrator exits successfully: {result.returncode}")
        check(output.exists(), "remote enrichment file is created locally")
        sidecar = enrichment_dir / f"{transcript_id}-enriched.json"
        results_log = log_dir / "pipeline-results.jsonl"
        if output.exists():
            content = output.read_text()
            check(content.startswith("# Remote Enrichment"), "workstation enrichment is used when local MLX probe fails")
            check("raw transcript from laptop" in content, "remote output is synced back")
        check(sidecar.exists(), "remote sidecar file is synced back")
        if results_log.exists():
            payload = json.loads(results_log.read_text().strip().splitlines()[-1])
            check(payload["transcript_id"] == transcript_id, "structured pipeline result includes transcript id")
            check(payload["metrics"].get("action_item_count") == 1, "structured pipeline result records sidecar metrics")
        check("LLM server not running" not in result.stdout, "remote workstation path skips local MLX failure branch")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)

if __name__ == "__main__":
    main()
