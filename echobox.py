#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from echobox_recorder import EchoboxRecorder
from echobox_recorder import EchoboxMenuBar
from echobox_recorder import EchoboxWatcher

from pipeline import actions as actions_module
from pipeline import clean as clean_module
from pipeline import demo as demo_module
from pipeline import fit as fit_module
from pipeline import list_calls as list_calls_module
from pipeline import markdown_preview as markdown_preview_module
from pipeline import read_config as read_config_module
from pipeline import search as search_module
from pipeline import setup as setup_module
from pipeline import show_config as show_config_module
from pipeline import status as status_module
from pipeline import summary as summary_module
from pipeline import smart_setup as smart_setup_module
from pipeline.enrich import ConfigError
from pipeline.enrich import get_config
from pipeline.enrich import load_config

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_DIR / "config" / "echobox.yaml"
VERSION_FILE = REPO_DIR / "VERSION"


@dataclass
class AppContext:
    repo_dir: Path
    config_path: Path
    version: str
    config: dict[str, str]
    paths: dict[str, str]

    @property
    def data_dir(self) -> Path:
        return Path(self.paths["DATA_DIR"])

    @property
    def transcript_dir(self) -> Path:
        return Path(self.paths["TRANSCRIPT_DIR"])

    @property
    def enrichment_dir(self) -> Path:
        return Path(self.paths["ENRICHMENT_DIR"])

    @property
    def report_dir(self) -> Path:
        return Path(self.paths["REPORT_DIR"])

    @property
    def log_dir(self) -> Path:
        return Path(self.paths["LOG_DIR"])

    @property
    def state_dir(self) -> Path:
        return Path(self.paths["STATE_DIR"])


def build_context(config_path: Path = DEFAULT_CONFIG) -> AppContext:
    try:
        config = load_config(config_path)
    except ConfigError:
        config = {}
    paths = read_config_module.resolve_paths(config_path)
    for key in ("LOG_DIR", "TRANSCRIPT_DIR", "ENRICHMENT_DIR", "REPORT_DIR", "STATE_DIR"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "dev"
    return AppContext(REPO_DIR, config_path, version, config, paths)


def report_slug_for_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
    return re.sub(r"-{2,}", "-", slug).strip("-")


def json_sidecar_for_enrichment(enrichment: Path) -> Path | None:
    sidecar = enrichment.with_suffix(".json")
    return sidecar if sidecar.exists() else None


def latest_enrichment(ctx: AppContext) -> Path | None:
    enrichments = sorted(ctx.enrichment_dir.glob("*.md"), reverse=True)
    return enrichments[0] if enrichments else None


def resolve_enrichment_input(ctx: AppContext, target: str | None) -> Path | None:
    if not target:
        return latest_enrichment(ctx)
    direct = Path(target).expanduser()
    if direct.is_file():
        return direct
    for candidate in (ctx.enrichment_dir / target, ctx.enrichment_dir / f"{target}-enriched.md"):
        if candidate.is_file():
            return candidate
    matches = sorted(ctx.enrichment_dir.glob(f"*{target}*.md"), reverse=True)
    return matches[0] if matches else None


def preview_markdown_file(path: Path) -> int:
    if shutil.which("glow"):
        return subprocess.run(["glow", "-s", "dark", str(path)], check=False).returncode
    if shutil.which("bat"):
        return subprocess.run(
            ["bat", "--paging=never", "--language", "markdown", str(path)],
            check=False,
        ).returncode
    print(markdown_preview_module.render_markdown(path.read_text(encoding="utf-8")))
    return 0


def run_python_module(main_func, argv: list[str]) -> int:
    original = sys.argv[:]
    sys.argv = argv
    try:
        return int(main_func() or 0)
    finally:
        sys.argv = original


def run_shell_script(script: Path, *args: str, extra_env: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    env.setdefault(
        "PATH",
        f"/opt/homebrew/bin:/usr/local/bin:{Path.home()}/bin:{Path.home()}/.local/bin:{env.get('PATH', '')}",
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(script), *args], check=False, env=env).returncode


def run_shell_script_capture(
    script: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault(
        "PATH",
        f"/opt/homebrew/bin:/usr/local/bin:{Path.home()}/bin:{Path.home()}/.local/bin:{env.get('PATH', '')}",
    )
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(script), *args],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def can_write_directory(path: Path) -> bool:
    path = path.expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".echobox-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _resolve_transcript(ctx: AppContext, target: str) -> Path | None:
    candidate = Path(target).expanduser()
    if candidate.is_file():
        return candidate
    by_name = ctx.transcript_dir / f"{target}.txt"
    if by_name.is_file():
        return by_name
    matches = sorted(ctx.transcript_dir.glob(f"*{target}*"), reverse=True)
    return matches[0] if matches else None


def _print_enrichment_summary(output: Path) -> None:
    if not output.exists():
        return
    summary_lines: list[str] = []
    capture = False
    for line in output.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Meeting Summary"):
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line.strip():
            summary_lines.append(line.strip())
        if len(summary_lines) >= 2:
            break

    action_count = 0
    speaker_count = 0
    sidecar = json_sidecar_for_enrichment(output)
    if sidecar:
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        action_count = len(data.get("action_items", []))
        speaker_count = len(data.get("speakers", []))

    if summary_lines:
        print("")
        print(f"  Summary: {' '.join(summary_lines)[:120]}")
    if action_count:
        print(f"  Action items: {action_count}")
    if speaker_count:
        print(f"  Speakers: {speaker_count}")
    if summary_lines or action_count or speaker_count:
        print("")
    print(f"  Preview: ./echobox preview {output}")
    print(f"  Next: ./echobox publish {output}")
    print("    Or: ./echobox open")


def cmd_version(ctx: AppContext, _args: argparse.Namespace) -> int:
    print(f"echobox {ctx.version}")
    return 0


def cmd_status(ctx: AppContext, _args: argparse.Namespace) -> int:
    return run_python_module(
        status_module.main,
        [
            "pipeline/status.py",
            str(ctx.config_path),
            str(ctx.transcript_dir),
            str(ctx.enrichment_dir),
            str(ctx.report_dir),
            str(ctx.log_dir),
        ],
    )


def cmd_enrich(ctx: AppContext, args: argparse.Namespace) -> int:
    transcript = _resolve_transcript(ctx, args.transcript)
    if transcript is None or not transcript.is_file():
        print(
            f"Error: transcript not found: {args.transcript}. Run 'echobox list' to see available calls.",
            file=sys.stderr,
        )
        return 1

    transcript_id = transcript.stem
    output = ctx.enrichment_dir / f"{transcript_id}-enriched.md"

    print(f"Enriching: {transcript}", flush=True)
    print(f"Output:    {output}", flush=True)
    print("", flush=True)

    workstation = os.environ.get("ECHOBOX_WORKSTATION", get_config(ctx.config, "workstation_ssh", ""))
    if workstation:
        remote_basename = re.sub(r"[^a-zA-Z0-9._-]", "_", transcript.name)
        remote_enriched = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{transcript_id}-enriched.md")
        print("Syncing transcript to workstation...")
        if subprocess.run(
            ["rsync", "-az", str(transcript), f"{workstation}:~/echobox-data/transcripts/{remote_basename}"],
            check=False,
        ).returncode != 0:
            return 1
        remote_cmd = (
            "cd ~/echobox && python3 pipeline/enrich.py "
            f"~/echobox-data/transcripts/{remote_basename} -o ~/echobox-data/enrichments/{remote_enriched}"
        )
        if args.verbose:
            remote_cmd += " --verbose"
        if subprocess.run(["ssh", "-o", "ConnectTimeout=10", workstation, remote_cmd], check=False).returncode != 0:
            return 1
        if subprocess.run(
            ["rsync", "-az", f"{workstation}:~/echobox-data/enrichments/{remote_enriched}", str(output)],
            check=False,
        ).returncode != 0:
            return 1
        subprocess.run(
            [
                "rsync",
                "-az",
                f"{workstation}:~/echobox-data/enrichments/{remote_enriched.removesuffix('.md')}.json",
                str(output.with_suffix(".json")),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        from pipeline import enrich as enrich_module

        enrich_args = ["pipeline/enrich.py", str(transcript), "-o", str(output)]
        if args.verbose:
            enrich_args.append("--verbose")
        return_code = run_python_module(enrich_module.main, enrich_args)
        if return_code != 0:
            return return_code

    print("")
    print(f"Enrichment complete: {output}")
    _print_enrichment_summary(output)
    return 0


def cmd_publish(ctx: AppContext, args: argparse.Namespace) -> int:
    enrichment = Path(args.enrichment).expanduser()
    if not enrichment.is_file():
        print(f"Error: enrichment not found: {args.enrichment}", file=sys.stderr)
        return 1
    print(f"Publishing: {enrichment}", flush=True)
    return run_shell_script(ctx.repo_dir / "pipeline" / "publish.sh", str(enrichment))


def cmd_preview(ctx: AppContext, args: argparse.Namespace) -> int:
    enrichment = resolve_enrichment_input(ctx, args.target)
    if enrichment is None:
        if args.target:
            print(f"No enrichment matched: {args.target}")
        else:
            print("No enrichments found yet.")
        print("Run: ./echobox enrich <transcript.txt>")
        return 1
    print(f"Previewing: {enrichment}")
    print("")
    return preview_markdown_file(enrichment)


def cmd_quality(ctx: AppContext, _args: argparse.Namespace) -> int:
    print("Echobox Quality Report")
    print("======================")
    print("")
    print("--- Pipeline Check ---")
    pipeline_rc, pipeline_stdout, pipeline_stderr = run_shell_script_capture(
        ctx.repo_dir / "quality" / "pipeline-check.sh"
    )
    if pipeline_stdout.strip():
        print(pipeline_stdout.rstrip())
    if pipeline_stderr.strip():
        print(pipeline_stderr.rstrip(), file=sys.stderr)
    print("")
    print("--- Context Check ---")
    context_rc, context_stdout, context_stderr = run_shell_script_capture(
        ctx.repo_dir / "quality" / "context-check.sh"
    )
    if context_stdout.strip():
        print(context_stdout.rstrip())
    if context_stderr.strip():
        print(context_stderr.rstrip(), file=sys.stderr)
    return 0 if pipeline_rc == 0 and context_rc == 0 else 1


def cmd_watch(ctx: AppContext, _args: argparse.Namespace) -> int:
    watcher_log = ctx.log_dir / "watcher.log"
    child_processes: list[subprocess.Popen] = []
    child_lock = threading.Lock()
    try:
        watcher_log.parent.mkdir(parents=True, exist_ok=True)
        with watcher_log.open("a", encoding="utf-8") as log_handle:
            log_handle.write("Starting built-in Echobox recorder\n")
    except OSError as exc:
        print(f"Error: cannot write watcher log: {watcher_log}", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("  Fix log_dir in config/echobox.yaml or make the directory writable, then retry.", file=sys.stderr)
        print("  Check: ./echobox status", file=sys.stderr)
        return 1
    print("Starting Echobox watcher...")
    print(f"Transcripts will be saved to: {ctx.transcript_dir}")
    print("Press Ctrl+C to stop.")
    print("")

    def reap_children() -> None:
        with child_lock:
            alive: list[subprocess.Popen] = []
            for child in child_processes:
                if child.poll() is None:
                    alive.append(child)
                    continue
                try:
                    child.wait(timeout=0)
                except Exception:
                    pass
            child_processes[:] = alive

    def emit(message: str) -> None:
        print(message)
        try:
            with watcher_log.open("a", encoding="utf-8") as log_handle:
                log_handle.write(f"{message}\n")
        except OSError:
            pass

    stop_reaper = threading.Event()

    def reap_children_forever() -> None:
        while not stop_reaper.wait(5):
            reap_children()

    reaper_thread = threading.Thread(target=reap_children_forever, daemon=True)
    reaper_thread.start()

    def on_meeting_end(transcript_path: Path) -> None:
        reap_children()
        emit(f"Meeting ended: {transcript_path.name}")
        child = subprocess.Popen(
            ["bash", str(ctx.repo_dir / "pipeline" / "orchestrator.sh"), transcript_path.stem],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with child_lock:
            child_processes.append(child)

    recorder = EchoboxRecorder(
        output_dir=ctx.transcript_dir,
        whisper_model=get_config(ctx.config, "whisper_model", "mlx-community/whisper-large-v3-mlx"),
        logger=emit,
    )
    watcher = EchoboxWatcher(recorder, on_meeting_end=on_meeting_end, logger=emit)

    use_menubar = (
        EchoboxMenuBar is not None
        and not os.environ.get("ECHOBOX_HEADLESS")
    )
    if use_menubar:
        emit("Watcher ready")
        app = EchoboxMenuBar(
            watcher,
            transcript_dir=ctx.transcript_dir,
            report_dir=ctx.report_dir,
        )
        try:
            app.run()
            return 0
        finally:
            stop_reaper.set()
            reaper_thread.join(timeout=1)
            reap_children()

    try:
        return watcher.run_forever()
    finally:
        stop_reaper.set()
        reaper_thread.join(timeout=1)
        reap_children()


def cmd_list(ctx: AppContext, _args: argparse.Namespace) -> int:
    return run_python_module(
        list_calls_module.main,
        ["pipeline/list_calls.py", str(ctx.transcript_dir), str(ctx.enrichment_dir), str(ctx.report_dir)],
    )


def cmd_actions(ctx: AppContext, _args: argparse.Namespace) -> int:
    return run_python_module(actions_module.main, ["pipeline/actions.py", str(ctx.enrichment_dir)])


def cmd_summary(ctx: AppContext, args: argparse.Namespace) -> int:
    return run_python_module(
        summary_module.main,
        ["pipeline/summary.py", str(ctx.enrichment_dir), *args.summary_args],
    )


def cmd_reprocess(ctx: AppContext, args: argparse.Namespace) -> int:
    transcript = _resolve_transcript(ctx, args.name)
    if transcript is None or not transcript.is_file():
        print(f"Error: no transcript matching '{args.name}'")
        print("Available:")
        for path in sorted(ctx.transcript_dir.glob("*.txt")):
            print(f"  {path.stem}")
        return 1

    base = transcript.stem
    enrichment = ctx.enrichment_dir / f"{base}-enriched.md"
    print(f"Reprocessing: {base}", flush=True)
    print("", flush=True)
    print("[1/2] Enriching...", flush=True)
    enrich_rc = cmd_enrich(ctx, argparse.Namespace(transcript=str(transcript), verbose=False))
    if enrich_rc != 0:
        return enrich_rc
    print("[2/2] Publishing...", flush=True)
    publish_rc = run_shell_script(ctx.repo_dir / "pipeline" / "publish.sh", str(enrichment))
    if publish_rc != 0:
        return publish_rc
    print("")
    print("Done.")
    print(f"  Preview:  echobox preview {base}")
    print("  Report:   echobox open")
    return 0


def cmd_setup(ctx: AppContext, _args: argparse.Namespace) -> int:
    return run_python_module(
        setup_module.main,
        [
            "pipeline/setup.py",
            str(ctx.config_path),
            str(ctx.repo_dir / "config" / "echobox.example.yaml"),
        ],
    )


def cmd_smart_setup(ctx: AppContext, args: argparse.Namespace) -> int:
    smart_setup_args = ["pipeline/smart_setup.py"]
    if args.format:
        smart_setup_args.extend(["--format", args.format])
    if args.with_calendar:
        smart_setup_args.append("--with-calendar")
    if args.days is not None:
        smart_setup_args.extend(["--days", str(args.days)])
    return run_python_module(smart_setup_module.main, smart_setup_args)


def cmd_search(ctx: AppContext, args: argparse.Namespace) -> int:
    return run_python_module(
        search_module.main,
        ["pipeline/search.py", args.term, str(ctx.enrichment_dir), str(ctx.transcript_dir)],
    )


def _try_open(target: str) -> bool:
    if shutil.which("open"):
        return (
            subprocess.run(
                ["open", target],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    if shutil.which("xdg-open"):
        return (
            subprocess.run(
                ["xdg-open", target],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    return False


def cmd_open(ctx: AppContext, args: argparse.Namespace) -> int:
    if not args.target:
        reports = sorted(ctx.report_dir.glob("*/report.html"), reverse=True)
        if reports:
            latest = reports[0]
            print(f"Opening: {latest}")
            if not _try_open(str(latest)):
                print(f"  File: {latest}")
                print("  No browser could open the report from this environment.")
                print("  Use './echobox preview' to inspect the latest enrichment, or open the file manually.")
            return 0

        state_path = ctx.state_dir / "last-report-url"
        latest_url = state_path.read_text(encoding="utf-8").strip() if state_path.exists() else ""
        if latest_url:
            print(f"Opening: {latest_url}")
            if not _try_open(latest_url):
                print(f"  URL: {latest_url}")
            return 0

        print("No reports found.")
        print("  Run: echobox enrich <transcript> && echobox publish <enrichment>")
        return 1

    direct = Path(args.target).expanduser()
    if direct.is_file():
        return 0 if _try_open(str(direct)) else 1
    report_path = ctx.report_dir / args.target / "report.html"
    if report_path.is_file():
        return 0 if _try_open(str(report_path)) else 1

    print(f"Report not found: {args.target}")
    available = sorted(ctx.report_dir.glob("*/report.html"))
    if available:
        print("Available:")
        for item in available:
            print(f"  {item.parent.name}")
    return 1


def cmd_clean(ctx: AppContext, args: argparse.Namespace) -> int:
    return run_python_module(
        clean_module.main,
        [
            "pipeline/clean.py",
            str(ctx.data_dir),
            str(ctx.transcript_dir),
            str(ctx.enrichment_dir),
            str(ctx.report_dir),
            str(ctx.log_dir),
            *args.clean_args,
        ],
    )


def cmd_config(ctx: AppContext, _args: argparse.Namespace) -> int:
    return run_python_module(show_config_module.main, ["pipeline/show_config.py", str(ctx.config_path)])


def cmd_test(ctx: AppContext, _args: argparse.Namespace) -> int:
    print("Echobox Smoke Tests")
    print("===================")
    print("")
    failures = 0
    for test_file in sorted((ctx.repo_dir / "tests").glob("test_*.py")):
        result = subprocess.run(
            [sys.executable, str(test_file)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            print(f"  [ok] {test_file.stem}")
        else:
            print(f"  [FAIL] {test_file.stem}")
            failures += 1
    print("")
    if failures:
        print(f"{failures} test(s) failed.")
        return 1
    print("All tests passed.")
    return 0


def cmd_fit(ctx: AppContext, args: argparse.Namespace) -> int:
    return run_python_module(fit_module.main, ["pipeline/fit.py", *args.fit_args, "--config", str(ctx.config_path)])


def cmd_demo(ctx: AppContext, _args: argparse.Namespace) -> int:
    report_dir = ctx.report_dir
    if not can_write_directory(report_dir):
        report_dir = Path(tempfile.gettempdir()) / "echobox-demo-reports"
        report_dir.mkdir(parents=True, exist_ok=True)
    return run_python_module(
        demo_module.main,
        ["pipeline/demo.py", str(ctx.repo_dir), str(ctx.config_path), str(report_dir), "open"],
    )


def cmd_transcribe(ctx: AppContext, args: argparse.Namespace) -> int:
    wav_input = Path(args.wav_file).expanduser().resolve()
    if not wav_input.is_file():
        print(f"Error: WAV file not found: {wav_input}", file=sys.stderr)
        return 1

    # Check if resampling is needed
    probe = subprocess.run(
        ["ffmpeg", "-i", str(wav_input)],
        capture_output=True,
        text=True,
    )
    probe_output = probe.stderr  # ffmpeg prints info to stderr
    needs_resample = True
    if "16000 Hz" in probe_output and "mono" in probe_output:
        needs_resample = False

    if needs_resample:
        print(f"Resampling to mono 16kHz...", flush=True)
        resampled = wav_input.parent / f"{wav_input.stem}_16k.wav"
        rc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_input), "-ar", "16000", "-ac", "1", str(resampled)],
            capture_output=True,
        ).returncode
        if rc != 0:
            print("Error: ffmpeg resampling failed", file=sys.stderr)
            return 1
        work_path = resampled
    else:
        work_path = wav_input

    recorder = EchoboxRecorder(
        output_dir=ctx.transcript_dir,
        whisper_model=get_config(ctx.config, "whisper_model", "mlx-community/whisper-large-v3-mlx"),
        logger=lambda msg: print(msg),
    )

    print(f"Transcribing: {wav_input.name}", flush=True)
    result = recorder._transcribe_wav(work_path)
    if isinstance(result, dict):
        result["_wav_path"] = str(work_path)

    # Use file modification time as a proxy for recording start
    from datetime import datetime
    started_at = datetime.fromtimestamp(wav_input.stat().st_mtime).astimezone()

    # Estimate duration from WAV file
    try:
        with wave.open(str(work_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration_seconds = max(1, frames // rate)
    except Exception:
        duration_seconds = 0

    transcript_body = recorder._format_transcript(started_at, duration_seconds, result)

    transcript_name = f"{wav_input.stem}.txt"
    transcript_path = ctx.transcript_dir / transcript_name
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(transcript_body, encoding="utf-8")

    # Clean up resampled file
    if needs_resample and work_path != wav_input and work_path.exists():
        work_path.unlink()

    print(f"Transcript saved: {transcript_path}")
    return 0


def cmd_serve(ctx: AppContext, args: argparse.Namespace) -> int:
    from pipeline.serve import start_server
    password = ctx.config.get("publish.password", "")
    if not password or password == "change-me":
        print("Error: set publish.password in config/echobox.yaml before serving.", file=sys.stderr)
        print("  Reports without a real password are publicly readable.", file=sys.stderr)
        return 1
    return start_server(ctx.report_dir, password, args.port, args.tunnel)


def custom_help(version: str) -> str:
    return f"""Echobox {version} - Self-hosted call intelligence pipeline

Getting started:
  echobox status              Check what's installed and configured
  echobox setup               Interactive config wizard
  echobox smart-setup         Probe machine and draft setup recommendations
  echobox fit                 Pick the best models for your hardware
  echobox demo                Try the pipeline on sample data

Daily use:
  echobox watch               Auto-record and process calls (macOS)
  echobox list                Show recent calls and their status
  echobox open [report]       Open a report in your browser
  echobox search <term>       Search across all calls
  echobox preview [call]      Preview enrichment in terminal
  echobox actions             Action items across all calls
  echobox summary [N|--month] Summary of calls, decisions, actions

Pipeline:
  echobox transcribe <wav>    Transcribe a WAV file with Whisper + optional diarization
  echobox enrich <file>       Run LLM enrichment on a transcript
  echobox publish <file>      Generate HTML report from enrichment
  echobox reprocess <name>    Re-enrich and re-publish a call
  echobox serve [--tunnel X]  Serve reports with password gate (local/tailscale/bore)

More:
  echobox clean [--older N] [--prune]  Show disk usage and optionally prune old data
  echobox config              Show parsed config values
  echobox quality             Run quality checks
  echobox test                Run smoke tests
  echobox version             Print version
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, prog="echobox")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-v", "--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", add_help=False)
    search_parser = subparsers.add_parser("search", add_help=False)
    search_parser.add_argument("term")
    open_parser = subparsers.add_parser("open", add_help=False)
    open_parser.add_argument("target", nargs="?")
    preview_parser = subparsers.add_parser("preview", add_help=False)
    preview_parser.add_argument("target", nargs="?")
    subparsers.add_parser("actions", add_help=False)
    summary_parser = subparsers.add_parser("summary", add_help=False)
    summary_parser.add_argument("summary_args", nargs=argparse.REMAINDER)
    reprocess_parser = subparsers.add_parser("reprocess", add_help=False)
    reprocess_parser.add_argument("name")
    enrich_parser = subparsers.add_parser("enrich", add_help=False)
    enrich_parser.add_argument("transcript")
    enrich_parser.add_argument("--verbose", action="store_true")
    publish_parser = subparsers.add_parser("publish", add_help=False)
    publish_parser.add_argument("enrichment")
    subparsers.add_parser("watch", add_help=False)
    subparsers.add_parser("setup", add_help=False)
    smart_setup_parser = subparsers.add_parser("smart-setup", add_help=False)
    smart_setup_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    smart_setup_parser.add_argument("--with-calendar", action="store_true")
    smart_setup_parser.add_argument("--days", type=int, default=14)
    subparsers.add_parser("status", add_help=False)
    fit_parser = subparsers.add_parser("fit", add_help=False)
    fit_parser.add_argument("fit_args", nargs=argparse.REMAINDER)
    subparsers.add_parser("config", add_help=False)
    subparsers.add_parser("quality", add_help=False)
    subparsers.add_parser("demo", add_help=False)
    subparsers.add_parser("test", add_help=False)
    clean_parser = subparsers.add_parser("clean", add_help=False)
    clean_parser.add_argument("clean_args", nargs=argparse.REMAINDER)
    serve_parser = subparsers.add_parser("serve", add_help=False)
    serve_parser.add_argument("--port", type=int, default=8090)
    serve_parser.add_argument("--tunnel", choices=["tailscale", "bore", ""], default="")
    transcribe_parser = subparsers.add_parser("transcribe", add_help=False)
    transcribe_parser.add_argument("wav_file")
    subparsers.add_parser("version", add_help=False)
    subparsers.add_parser("help", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "dev"
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.help or (args.version and not args.command):
        if args.version and not args.help:
            print(f"echobox {version}")
            return 0
        print(custom_help(version))
        return 0
    if args.command in (None, "help"):
        print(custom_help(version))
        return 0

    ctx = build_context()
    handlers = {
        "actions": cmd_actions,
        "clean": cmd_clean,
        "config": cmd_config,
        "demo": cmd_demo,
        "enrich": cmd_enrich,
        "fit": cmd_fit,
        "list": cmd_list,
        "open": cmd_open,
        "preview": cmd_preview,
        "publish": cmd_publish,
        "quality": cmd_quality,
        "reprocess": cmd_reprocess,
        "search": cmd_search,
        "serve": cmd_serve,
        "setup": cmd_setup,
        "smart-setup": cmd_smart_setup,
        "status": cmd_status,
        "summary": cmd_summary,
        "test": cmd_test,
        "transcribe": cmd_transcribe,
        "version": cmd_version,
        "watch": cmd_watch,
    }
    return handlers[args.command](ctx, args)


if __name__ == "__main__":
    raise SystemExit(main())
