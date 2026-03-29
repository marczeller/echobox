#!/usr/bin/env python3
"""Print pipeline dependency and data status."""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import load_config


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return ""
    return (result.stdout or result.stderr).strip()


def module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def is_blackhole_configured() -> bool:
    try:
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return "BlackHole" in result.stdout


def can_reach_models(mlx_url: str) -> bool:
    models_url = mlx_url.removesuffix("/chat/completions") + "/models"
    try:
        result = subprocess.run(
            ["curl", "-sf", models_url],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def can_reach_ssh(target: str) -> bool:
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", target, "echo ok"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return "ok" in result.stdout


def file_count(directory: Path, pattern: str) -> int:
    return sum(1 for _ in directory.glob(pattern))


def is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".echobox-write-test.{Path.cwd().name}.{Path(__file__).stem}"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def main() -> int:
    if len(sys.argv) < 6:
        print("Usage: python3 pipeline/status.py <config> <transcript_dir> <enrichment_dir> <report_dir> <log_dir>")
        return 1

    config_path = Path(sys.argv[1])
    transcript_dir = Path(sys.argv[2])
    enrichment_dir = Path(sys.argv[3])
    report_dir = Path(sys.argv[4])
    log_dir = Path(sys.argv[5])

    ready = True
    issues: list[str] = []

    print("Echobox Pipeline Status")
    print("=======================")
    print("")
    print("Components:")

    if config_path.exists():
        try:
            config = load_config(config_path)
            if not isinstance(config, dict):
                raise ValueError("config did not parse to a dictionary")
            print(f"  Config:         valid (loaded {len(config)} values)")
        except Exception as exc:
            print("  Config:         INVALID")
            ready = False
            issues.append(f"  - Fix config parse errors in {config_path}")
            issues.append(f"    {exc}")
            config = {}
    else:
        print("  Config:         NOT FOUND")
        ready = False
        issues.append("  - Run ./install.sh or create config/echobox.yaml")
        config = {}

    if module_importable("echobox_recorder"):
        print("  Recorder:       importable")
    else:
        print("  Recorder:       NOT FOUND")
        ready = False
        issues.append("  - Ensure the built-in echobox_recorder package is present")

    if has_command("ffmpeg"):
        print("  ffmpeg:         installed")
    else:
        print("  ffmpeg:         NOT FOUND")
        ready = False
        issues.append("  - Install ffmpeg: brew install ffmpeg")

    if module_importable("yaml"):
        print("  PyYAML:         importable")
    else:
        print("  PyYAML:         NOT FOUND")
        ready = False
        issues.append("  - Install PyYAML: python3 -m pip install --user pyyaml")

    if module_importable("mlx_whisper"):
        print("  mlx-whisper:    importable")
    else:
        print("  mlx-whisper:    NOT FOUND")
        ready = False
        issues.append("  - Install mlx-whisper: python3 -m pip install --user mlx-whisper")

    if module_importable("sounddevice"):
        print("  sounddevice:    importable")
    else:
        print("  sounddevice:    NOT FOUND")
        ready = False
        issues.append("  - Install sounddevice: python3 -m pip install --user sounddevice")

    if module_importable("pyannote.audio"):
        print("  pyannote:       importable")
    else:
        print("  pyannote:       NOT FOUND")
        issues.append("  - Install pyannote.audio: python3 -m pip install --user pyannote.audio")
        issues.append("    Then accept the model license and set HF_TOKEN")

    if is_blackhole_configured():
        print("  BlackHole:      detected")
    else:
        print("  BlackHole:      NOT CONFIGURED")
        ready = False
        issues.append("  - Install BlackHole: brew install blackhole-2ch")

    mlx_url = config.get("mlx_url", "http://localhost:8090/v1/chat/completions")
    if can_reach_models(mlx_url):
        print(f"  MLX server:     running ({mlx_url})")
    else:
        print(f"  MLX server:     NOT RUNNING ({mlx_url})")
        ready = False
        mlx_model = config.get("mlx_model", "")
        if mlx_model:
            issues.append(f"  - Start your LLM server: mlx_lm.server --model {mlx_model} --port 8090")
        else:
            issues.append("  - Start your LLM server, then retry echobox demo or echobox enrich")

    workstation = config.get("workstation_ssh", "")
    if workstation:
        if can_reach_ssh(workstation):
            print(f"  Workstation:    reachable ({workstation})")
        else:
            print(f"  Workstation:    UNREACHABLE ({workstation})")
    else:
        print("  Workstation:    single-machine mode")

    print("")
    print("Data:")
    print(f"  Transcripts:    {file_count(transcript_dir, '*.txt')}")
    print(f"  Enrichments:    {file_count(enrichment_dir, '*.md')}")
    print(f"  Reports:        {file_count(report_dir, '*/report.html')}")
    print(f"  Transcript dir: {transcript_dir}")
    print(f"  Enrichment dir: {enrichment_dir}")
    print(f"  Report dir:     {report_dir}")
    print(f"  Log dir:        {log_dir}")

    print("")
    print("Write Access:")
    for label, path in (
        ("Transcript", transcript_dir),
        ("Enrichment", enrichment_dir),
        ("Report", report_dir),
        ("Log", log_dir),
    ):
        if is_writable(path):
            print(f"  {label}:      writable")
        else:
            print(f"  {label}:      NOT WRITABLE ({path})")
            ready = False
            issues.append(f"  - Fix write access for {path}")

    print("")
    if ready:
        print("  Pipeline: READY")
    else:
        print("  Pipeline: NOT READY — missing components:")
        for issue in issues:
            print(issue)

    print("")
    print(f"Config: {config_path}")
    if config_path.exists():
        print("  Status: present")
    else:
        print("  Status: NOT FOUND — run: echobox setup")

    print("")
    if ready:
        print("Next:")
        print("  ./echobox demo         Validate the pipeline on sample data")
        print("  ./echobox watch        Start recording real calls")
    else:
        print("Suggested next steps:")
        print("  ./install.sh           Fix missing dependencies interactively")
        if config_path.exists():
            print("  ./echobox fit          Re-check model choices after deps are installed")
        else:
            print("  ./echobox setup        Create config/echobox.yaml")
        print("  ./echobox demo         Check the user-facing output format")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
