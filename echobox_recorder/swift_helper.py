"""Subprocess driver for the echobox-capture Swift helper binary.

The Swift helper runs for the lifetime of one recording session. It writes
audio/mic.wav and session.json into a session directory, and emits JSONL
events on stdout (started, heartbeat, level, partial, final, stopped, error).

This module owns the lifecycle of that subprocess and exposes:
    - SwiftHelperBackend: a CaptureBackend implementation usable by the watcher
    - SwiftHelperSession: per-session state surfaced to the menubar
    - parse_jsonl_event: helper used by both the driver and tests

Design notes:
    - stdout carries structured JSONL only; stderr carries free-text logs.
    - We monitor heartbeats; if none arrive for HEARTBEAT_TIMEOUT seconds, the
      helper is presumed dead and the session is marked interrupted.
    - SIGTERM is sent on stop; the helper finishes the WAV header, writes
      session.json with capture_status=completed, and exits cleanly.
    - On crash mid-call we keep the partial WAV and let the post-call pipeline
      transcribe whatever was captured (mlx-whisper handles partial files).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HEARTBEAT_TIMEOUT = 6.0  # seconds; helper sends one heartbeat per second
STOP_FLUSH_TIMEOUT = 8.0  # seconds; how long we wait after SIGTERM for clean exit


def find_helper_binary() -> Path | None:
    """Locate the echobox-capture binary.

    Search order:
        1. ECHOBOX_CAPTURE_BIN environment variable
        2. <repo>/bin/echobox-capture (installed by install.sh)
        3. <repo>/swift/echobox-capture/.build/{release,debug}/echobox-capture
    """
    env = os.environ.get("ECHOBOX_CAPTURE_BIN")
    if env:
        path = Path(env).expanduser()
        if path.exists():
            return path
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root / "bin" / "echobox-capture",
        repo_root / "swift" / "echobox-capture" / ".build" / "release" / "echobox-capture",
        repo_root / "swift" / "echobox-capture" / ".build" / "debug" / "echobox-capture",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def parse_jsonl_event(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


@dataclass
class SwiftHelperSession:
    """Mutable state for one recording session driven by the Swift helper."""

    session_id: str
    session_dir: Path
    started_at: datetime
    process: subprocess.Popen
    source: str
    sample_rate: int
    channels: int
    wav_path: Path
    transcript_path: Path
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_level_rms: float = 0.0
    frames_written: int = 0
    live_partials: list[dict[str, Any]] = field(default_factory=list)
    live_finals: list[dict[str, Any]] = field(default_factory=list)
    capture_status: str = "starting"
    error_messages: list[str] = field(default_factory=list)
    reader_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    stop_requested: bool = False
    stopped: bool = False
    stopped_at: datetime | None = None
    duration_seconds: float = 0.0


class SwiftHelperBackend:
    """CaptureBackend that drives the echobox-capture Swift helper."""

    def __init__(
        self,
        *,
        sessions_root: Path,
        binary_path: Path | None = None,
        source: str = "default-input",
        sample_rate: int = 16_000,
        channels: int = 1,
        device_name: str | None = None,
        live_transcript: bool = False,
        whisperkit_model: str = "openai_whisper-tiny",
        logger: Callable[[str], None] | None = None,
        on_event: Callable[[SwiftHelperSession, dict[str, Any]], None] | None = None,
    ) -> None:
        self.sessions_root = Path(sessions_root).expanduser()
        self.binary_path = binary_path or find_helper_binary()
        self.source = source
        self.sample_rate = sample_rate
        self.channels = channels
        self.device_name = device_name
        self.live_transcript = live_transcript
        self.whisperkit_model = whisperkit_model
        self.logger = logger or (lambda _msg: None)
        self.on_event = on_event
        self._session: SwiftHelperSession | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._session is not None and not self._session.stopped

    @property
    def session(self) -> SwiftHelperSession | None:
        return self._session

    def ensure_binary(self) -> Path:
        if self.binary_path is None or not self.binary_path.exists():
            raise RuntimeError(
                "echobox-capture binary not found. "
                "Build it with: cd swift/echobox-capture && swift build -c release"
            )
        return self.binary_path

    def start(
        self,
        session_id: str,
        *,
        transcript_path: Path,
    ) -> SwiftHelperSession:
        with self._lock:
            if self._session is not None and not self._session.stopped:
                raise RuntimeError("SwiftHelperBackend already active")

            binary = self.ensure_binary()
            session_dir = self.sessions_root / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "audio").mkdir(exist_ok=True)
            wav_path = session_dir / "audio" / "mic.wav"
            # Spotlight sentinel — keep raw audio out of the index.
            try:
                (session_dir / ".metadata_never_index").touch()
            except OSError:
                pass

            argv = [
                str(binary),
                "--session-dir", str(session_dir),
                "--source", self.source,
                "--sample-rate", str(self.sample_rate),
                "--channels", str(self.channels),
            ]
            if self.device_name:
                argv += ["--device-name", self.device_name]
            if self.live_transcript:
                argv += ["--live-transcript", "--whisperkit-model", self.whisperkit_model]

            self.logger(f"spawning capture helper: {' '.join(argv)}")
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=1,
                    text=True,
                )
            except OSError as exc:
                raise RuntimeError(f"failed to launch echobox-capture: {exc}") from exc

            session = SwiftHelperSession(
                session_id=session_id,
                session_dir=session_dir,
                started_at=datetime.now().astimezone(),
                process=proc,
                source=self.source,
                sample_rate=self.sample_rate,
                channels=self.channels,
                wav_path=wav_path,
                transcript_path=transcript_path,
            )
            self._session = session

            session.reader_thread = threading.Thread(
                target=self._read_stdout,
                args=(session,),
                daemon=True,
                name=f"echobox-capture-stdout-{session_id}",
            )
            session.reader_thread.start()
            session.stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(session,),
                daemon=True,
                name=f"echobox-capture-stderr-{session_id}",
            )
            session.stderr_thread.start()
            return session

    def _read_stdout(self, session: SwiftHelperSession) -> None:
        proc = session.process
        assert proc.stdout is not None
        live_path = session.session_dir / "transcript.live.jsonl"
        try:
            live_fp = live_path.open("a", buffering=1, encoding="utf-8")
        except OSError as exc:
            self.logger(f"cannot open {live_path}: {exc}")
            live_fp = None
        try:
            for raw in proc.stdout:
                raw_stripped = raw.rstrip("\n")
                if live_fp is not None and raw_stripped:
                    live_fp.write(raw_stripped + "\n")
                event = parse_jsonl_event(raw)
                if event is None:
                    continue
                self._handle_event(session, event)
        finally:
            if live_fp is not None:
                try:
                    live_fp.close()
                except OSError:
                    pass
        # When stdout closes the helper has exited; mark stopped if not already.
        with self._lock:
            if not session.stopped:
                session.stopped = True
                session.stopped_at = datetime.now().astimezone()

    def _read_stderr(self, session: SwiftHelperSession) -> None:
        proc = session.process
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self.logger(f"capture-helper: {line}")

    def _handle_event(self, session: SwiftHelperSession, event: dict[str, Any]) -> None:
        kind = event.get("type")
        now = time.monotonic()
        if kind == "started":
            session.capture_status = "recording"
            self.logger(
                f"capture-helper started: source={event.get('source')} "
                f"sr={event.get('sample_rate')}"
            )
        elif kind == "heartbeat":
            session.last_heartbeat = now
            frames = event.get("frames_written")
            if isinstance(frames, (int, float)):
                session.frames_written = int(frames)
        elif kind == "level":
            rms = event.get("rms")
            if isinstance(rms, (int, float)):
                session.last_level_rms = float(rms)
        elif kind == "partial":
            session.live_partials.append(event)
        elif kind == "final":
            session.live_finals.append(event)
        elif kind == "error":
            msg = str(event.get("msg") or "")
            session.error_messages.append(msg)
            self.logger(f"capture-helper error: {msg}")
        elif kind == "stopped":
            session.stopped = True
            session.stopped_at = datetime.now().astimezone()
            duration = event.get("duration_seconds")
            if isinstance(duration, (int, float)):
                session.duration_seconds = float(duration)
            frames = event.get("frames_written")
            if isinstance(frames, (int, float)):
                session.frames_written = int(frames)
        if self.on_event is not None:
            try:
                self.on_event(session, event)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger(f"on_event callback failed: {exc}")

    def stop(self) -> SwiftHelperSession:
        with self._lock:
            session = self._session
            if session is None:
                raise RuntimeError("SwiftHelperBackend is not active")
            session.stop_requested = True
        proc = session.process
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=STOP_FLUSH_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.logger("capture-helper did not exit on SIGTERM, sending SIGKILL")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if session.reader_thread is not None:
            session.reader_thread.join(timeout=2)
        if session.stderr_thread is not None:
            session.stderr_thread.join(timeout=2)
        with self._lock:
            session.stopped = True
            if session.capture_status == "recording":
                session.capture_status = "completed"
            if session.stopped_at is None:
                session.stopped_at = datetime.now().astimezone()
            self._session = None
        return session

    def check_health(self) -> str | None:
        """Return a non-None status string if the helper is unhealthy."""
        session = self._session
        if session is None:
            return None
        if session.process.poll() is not None and not session.stopped:
            return "helper exited unexpectedly"
        elapsed = time.monotonic() - session.last_heartbeat
        if elapsed > HEARTBEAT_TIMEOUT and session.capture_status == "recording":
            return f"no heartbeat for {elapsed:.1f}s"
        return None


def session_id_from_hint(hint: str, started_at: datetime | None = None) -> str:
    if started_at is None:
        started_at = datetime.now().astimezone()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in hint.lower()) or "call"
    return f"{started_at.strftime('%Y-%m-%d_%H-%M')}_{safe}"
