#!/usr/bin/env python3
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PASS = FAIL = 0
REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"

def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")

def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-enrich-"))
    modules_dir = tmp / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    try:
        (modules_dir / "yaml.py").write_text("import json\n\ndef safe_load(stream):\n    return json.loads(stream.read())\n")
        fixture_text = (FIXTURES / "2026-03-15_10-00_roadmap-sync-enriched.md").read_text()
        sitecustomize = '''import io, json, urllib.request\n\nFIXTURE = %r\n\nclass _Resp:\n    def __init__(self, body): self.body = body\n    def read(self): return self.body\n    def __enter__(self): return self\n    def __exit__(self, *args): return False\n\ndef fake_urlopen(req, timeout=0):\n    url = req.full_url if hasattr(req, "full_url") else req\n    if str(url).endswith("/models"):\n        return _Resp(json.dumps({"data": [{"id": "demo-model"}]}).encode())\n    return _Resp(json.dumps({"choices": [{"message": {"content": FIXTURE}}]}).encode())\n\nurllib.request.urlopen = fake_urlopen\n''' % fixture_text
        (modules_dir / "sitecustomize.py").write_text(sitecustomize)
        config_path = tmp / "echobox.yaml"
        config_path.write_text(json.dumps({"mlx_url": "http://127.0.0.1:8090/v1/chat/completions", "mlx_model": "demo-model", "context_sources": {"calendar": {"enabled": False}}}))
        output_path = tmp / "enriched.md"
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{modules_dir}:{env.get('PYTHONPATH', '')}"
        result = subprocess.run([sys.executable, "pipeline/enrich.py", str(FIXTURES / "2026-03-15_10-00_roadmap-sync.txt"), "--config", str(config_path), "--output", str(output_path), "--verbose"], cwd=REPO, env=env, capture_output=True, text=True, timeout=30)
        stderr = result.stderr
        check(result.returncode == 0, f"enrich --verbose exits successfully: {result.returncode}")
        for snippet in ["Loading config...", "Parsing transcript metadata...", "Classifying meeting type...", "Curating context...", "Calling LLM (", "LLM response:", "Extracting structured data...", "Writing enrichment + JSON sidecar"]:
            check(snippet in stderr, f"verbose logs {snippet}")
        check("[0." in stderr, "verbose logs timing prefix")
        check(output_path.exists(), "verbose enrich writes markdown output")
        check(output_path.with_suffix(".json").exists(), "verbose enrich writes JSON sidecar")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)

if __name__ == "__main__":
    main()
