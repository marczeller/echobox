#!/usr/bin/env python3
"""Print pipeline dependency and data status."""
from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import load_config


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def module_importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def sounddevice_summary() -> tuple[bool, str]:
    try:
        import sounddevice as sd
    except Exception:
        return False, "not importable"
    try:
        devices = sd.query_devices()
    except Exception as exc:
        return False, f"query failed: {exc}"
    if not devices:
        return False, "no devices visible"
    inputs = []
    outputs = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        name = str(device.get("name", "unknown"))
        try:
            if int(device.get("max_input_channels", 0) or 0) > 0:
                inputs.append(name)
            if int(device.get("max_output_channels", 0) or 0) > 0:
                outputs.append(name)
        except (TypeError, ValueError):
            continue
    return True, f"{len(inputs)} inputs, {len(outputs)} outputs"


def current_audio(kind: str) -> str:
    sas = shutil.which("SwitchAudioSource")
    if not sas:
        return "unknown (SwitchAudioSource missing)"
    try:
        result = subprocess.run(
            [sas, "-c", "-t", kind],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:
        return f"unknown ({exc})"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def swift_helper_binary_status(repo_dir: Path) -> tuple[bool, str]:
    candidates = (
        repo_dir / "swift" / "echobox-capture" / ".build" / "release" / "echobox-capture",
        repo_dir / "swift" / "echobox-capture" / ".build" / "debug" / "echobox-capture",
    )
    for candidate in candidates:
        if candidate.exists():
            return True, str(candidate)
    return False, "not built"


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
    except (OSError, subprocess.SubprocessError):
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
    except (OSError, subprocess.SubprocessError):
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
    except OSError:
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

    capture_backend = str(config.get("capture.backend", "sounddevice"))
    if capture_backend == "screencapturekit":
        capture_source = "screencapturekit"
    else:
        capture_source = str(config.get("capture.source", "default-input"))

    if module_importable("mlx_whisper"):
        print("  mlx-whisper:    importable")
    else:
        print("  mlx-whisper:    NOT FOUND")
        ready = False
        issues.append("  - Install mlx-whisper: python3 -m pip install --user mlx-whisper")

    sound_ok, sound_detail = sounddevice_summary()
    if sound_ok:
        print(f"  sounddevice:    importable ({sound_detail})")
    else:
        print(f"  sounddevice:    NOT READY ({sound_detail})")
        if capture_backend == "sounddevice":
            ready = False
            if sound_detail == "not importable":
                issues.append("  - Install sounddevice: python3 -m pip install --user sounddevice")
            else:
                issues.append("  - Run Echobox in a user GUI session with microphone/audio-device access")

    print(f"  Audio input:    {current_audio('input')}")
    print(f"  Audio output:   {current_audio('output')}")
    print(f"  Capture:        {capture_backend} (source={capture_source})")
    if capture_backend in {"swift_helper", "screencapturekit"}:
        helper_ok, helper_detail = swift_helper_binary_status(Path(__file__).resolve().parent.parent)
        if helper_ok:
            print(f"  Swift helper:   built ({helper_detail})")
        else:
            print(f"  Swift helper:   NOT BUILT ({helper_detail})")
            ready = False
            issues.append("  - Build the helper: cd swift/echobox-capture && swift build -c release")
        if capture_backend == "screencapturekit":
            print("  SCK permission: grant Screen & System Audio Recording if prompted")

    if module_importable("pyannote.audio"):
        print("  pyannote:       importable")
    else:
        print("  pyannote:       NOT FOUND")
        ready = False
        issues.append("  - Install pyannote.audio: python3 -m pip install --user pyannote.audio")
        issues.append("    Then accept the model license and set HF_TOKEN")

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
            print(f"  {label:<12} writable")
        else:
            print(f"  {label:<12} NOT WRITABLE ({path})")
            ready = False
            issues.append(f"  - Fix write access for {path} or update the configured directory")

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
        print("  Status: NOT FOUND — run: ./install.sh or ./echobox setup")

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
