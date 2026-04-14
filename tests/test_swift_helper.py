"""Pure-Python tests for SwiftHelperBackend.

Monkeypatches subprocess.Popen with a fake process that yields a canned JSONL
stream, so we can exercise the event parser, session state, live.jsonl
persistence, health check, and stop() lifecycle without building Swift or
running WhisperKit. Keeps CI/tests fast and hermetic.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from echobox_recorder import swift_helper
from echobox_recorder.swift_helper import (
    SwiftHelperBackend,
    SwiftHelperSession,
    parse_jsonl_event,
    session_id_from_hint,
)


CANNED_EVENTS = [
    '{"type":"transcriber_loading","model":"openai_whisper-tiny"}\n',
    '{"type":"started","session_id":"test","source":"test-signal","sample_rate":16000,"channels":1,"wav_path":"/tmp/ignored"}\n',
    '{"type":"heartbeat","frames_written":16000}\n',
    '{"type":"level","rms":0.12}\n',
    '{"type":"transcriber_ready","model":"openai_whisper-tiny"}\n',
    '{"type":"partial","text":"hello","session_sample_offset":16000}\n',
    '{"type":"final","text":"hello world","session_sample_offset":32000}\n',
    '{"type":"heartbeat","frames_written":32000}\n',
    '{"type":"stopped","frames_written":48000,"duration_seconds":3.0}\n',
]


class FakeProcess:
    """Minimal Popen stand-in that satisfies SwiftHelperBackend."""

    def __init__(self, events: list[str]) -> None:
        self.stdout = io.StringIO("".join(events))
        self.stderr = io.StringIO("")
        self._returncode: int | None = None
        self._signals: list[int] = []

    def poll(self) -> int | None:
        return self._returncode

    def send_signal(self, sig: int) -> None:
        self._signals.append(sig)
        self._returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = 0
        return 0

    def kill(self) -> None:
        self._returncode = -9


class ParseJsonlTests(unittest.TestCase):
    def test_empty_line_returns_none(self) -> None:
        self.assertIsNone(parse_jsonl_event(""))
        self.assertIsNone(parse_jsonl_event("   \n"))

    def test_invalid_json_returns_none(self) -> None:
        self.assertIsNone(parse_jsonl_event("not json"))

    def test_non_dict_returns_none(self) -> None:
        self.assertIsNone(parse_jsonl_event("[1, 2, 3]"))

    def test_valid_event(self) -> None:
        evt = parse_jsonl_event('{"type":"heartbeat","frames_written":42}')
        self.assertEqual(evt, {"type": "heartbeat", "frames_written": 42})


class SessionIdTests(unittest.TestCase):
    def test_slug_is_normalised(self) -> None:
        from datetime import datetime

        dt = datetime(2025, 1, 2, 15, 30)
        sid = session_id_from_hint("Team Sync (weekly)", dt)
        self.assertTrue(sid.startswith("2025-01-02_15-30_"))
        self.assertNotIn(" ", sid)
        self.assertNotIn("(", sid)


class SwiftHelperBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.sessions_root = self.tmpdir / "sessions"
        self.sessions_root.mkdir()
        # Fake the binary path so ensure_binary() passes without a real build.
        self.fake_binary = self.tmpdir / "fake-capture"
        self.fake_binary.write_text("#!/bin/sh\nexit 0\n")
        self.fake_binary.chmod(0o755)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_backend(self) -> SwiftHelperBackend:
        return SwiftHelperBackend(
            sessions_root=self.sessions_root,
            binary_path=self.fake_binary,
            source="test-signal",
        )

    def _drive_fake_session(self, backend: SwiftHelperBackend, events: list[str]) -> SwiftHelperSession:
        fake = FakeProcess(events)
        with mock.patch.object(subprocess, "Popen", return_value=fake):
            session = backend.start(
                session_id="test-session",
                transcript_path=self.tmpdir / "test-session.txt",
            )
        # Wait for the reader thread to drain the fake stdout.
        for _ in range(200):
            if session.stopped:
                break
            time.sleep(0.01)
        return session

    def test_event_stream_updates_session_state(self) -> None:
        backend = self._make_backend()
        session = self._drive_fake_session(backend, CANNED_EVENTS)
        self.assertEqual(session.capture_status, "recording")
        self.assertGreaterEqual(session.frames_written, 32000)
        self.assertEqual([p["text"] for p in session.live_partials], ["hello"])
        self.assertEqual([f["text"] for f in session.live_finals], ["hello world"])
        self.assertTrue(session.stopped)
        self.assertEqual(session.duration_seconds, 3.0)

    def test_live_jsonl_is_persisted(self) -> None:
        backend = self._make_backend()
        session = self._drive_fake_session(backend, CANNED_EVENTS)
        live_path = session.session_dir / "transcript.live.jsonl"
        self.assertTrue(live_path.exists())
        lines = live_path.read_text().splitlines()
        self.assertEqual(len(lines), len(CANNED_EVENTS))
        # First line is the transcriber_loading event.
        self.assertIn("transcriber_loading", lines[0])
        self.assertIn("stopped", lines[-1])

    def test_on_event_callback_receives_every_event(self) -> None:
        seen: list[str] = []

        def cb(_session: SwiftHelperSession, event: dict) -> None:
            seen.append(event.get("type", ""))

        backend = self._make_backend()
        backend.on_event = cb
        self._drive_fake_session(backend, CANNED_EVENTS)
        self.assertEqual(
            seen,
            [
                "transcriber_loading",
                "started",
                "heartbeat",
                "level",
                "transcriber_ready",
                "partial",
                "final",
                "heartbeat",
                "stopped",
            ],
        )

    def test_check_health_flags_dead_helper(self) -> None:
        backend = self._make_backend()
        fake = FakeProcess(['{"type":"started","session_id":"x","source":"test-signal","sample_rate":16000,"channels":1,"wav_path":"/tmp"}\n'])
        with mock.patch.object(subprocess, "Popen", return_value=fake):
            backend.start(session_id="x", transcript_path=self.tmpdir / "x.txt")
        # Drain
        for _ in range(50):
            if backend._session and backend._session.capture_status == "recording":
                break
            time.sleep(0.01)
        # Force the fake to "die" without a stopped event, and simulate a stale
        # heartbeat by rewinding last_heartbeat far into the past.
        assert backend._session is not None
        backend._session.last_heartbeat = time.monotonic() - 100
        backend._session.capture_status = "recording"
        # Fake poll() returning None means process "alive" — but no heartbeat.
        fake._returncode = None
        status = backend.check_health()
        self.assertIsNotNone(status)
        self.assertIn("heartbeat", status)


if __name__ == "__main__":
    unittest.main()
