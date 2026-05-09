#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from echobox_recorder.watcher import BROWSER_SCRIPTS, DetectionResult, EchoboxWatcher
from echobox_recorder.recorder import EchoboxRecorder

PASS = 0
FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


class _FakeSession:
    backend = "sounddevice"


class FakeRecorder(EchoboxRecorder):
    def __init__(self, output_dir: Path):
        super().__init__(output_dir, "demo-model")
        self.stop_calls = 0

    def start(self, session_hint: str = "call"):
        self._session = _FakeSession()
        # Mirror EchoboxRecorder.start(): seed the silence timer so a fresh
        # session is not immediately silent.
        import time as _t
        self._last_sound_at = _t.monotonic()
        return self._session

    def stop(self):
        self._session = None
        self._last_sound_at = 0.0
        self.stop_calls += 1
        return self.output_dir / f"fake-{self.stop_calls}.txt"


class BrowserWatcher(EchoboxWatcher):
    def __init__(self, recorder: EchoboxRecorder, responses: dict[str, str]):
        super().__init__(recorder, start_cooldown=0)
        self.responses = responses

    def _run_osascript(self, script: str) -> str:
        return self.responses.get(script, "")

    def _pgrep_pids(self, app_name: str) -> list[int]:
        return []


class NativeWatcher(EchoboxWatcher):
    def __init__(self, recorder: EchoboxRecorder, pids: dict[str, list[int]], active: set[int]):
        super().__init__(recorder, start_cooldown=0)
        self.pids = pids
        self.active = active

    def _run_osascript(self, script: str) -> str:
        return ""

    def _pgrep_pids(self, app_name: str) -> list[int]:
        return self.pids.get(app_name, [])

    def _coreaudio_process_has_input(self, pid: int) -> bool:
        return pid in self.active


class CooldownWatcher(EchoboxWatcher):
    def __init__(self, recorder: EchoboxRecorder):
        super().__init__(recorder, start_cooldown=5)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-watcher-"))
    try:
        browser = BrowserWatcher(
            FakeRecorder(tmp),
            {
                BROWSER_SCRIPTS["Google Chrome"]: "https://example.com\nhttps://meet.google.com/abc-defg-hij?token=secret",
            },
        )
        detection = browser.detect_meeting()
        check(detection is not None and detection.source == "google-meet", "browser detection scans all tabs")
        check(
            detection is not None and "meet.google.com" in detection.detail,
            "browser detection returns matching background tab detail",
        )
        check(
            detection is not None and "?" not in detection.detail and "secret" not in detection.detail,
            "browser detection strips query strings from logged meeting URLs",
        )

        landing = BrowserWatcher(
            FakeRecorder(tmp),
            {
                BROWSER_SCRIPTS["Arc"]: "https://meet.google.com/landing",
            },
        )
        check(landing.detect_meeting() is None, "browser detection ignores Meet landing page")

        concatenated = BrowserWatcher(
            FakeRecorder(tmp),
            {
                BROWSER_SCRIPTS["Arc"]: (
                    "https://example.com/path?secret=abc"
                    "https://meet.google.com/abc-defg-hij?authuser=0&token=secret"
                    "https://mail.google.com/mail/u/0/#inbox"
                ),
            },
        )
        check(
            concatenated.detect_meeting() is None,
            "browser detection does not mine concatenated browser dumps for hidden meeting URLs",
        )

        zoom = BrowserWatcher(
            FakeRecorder(tmp),
            {
                BROWSER_SCRIPTS["Arc"]: "https://app.zoom.us/wc/3613949630/join?fromPWA=1&token=secret",
            },
        )
        detection = zoom.detect_meeting()
        check(detection is not None and detection.source == "zoom", "browser detection accepts active Zoom web client")
        check(
            detection is not None and detection.detail.endswith("/wc/3613949630/join"),
            "Zoom detection detail is sanitized",
        )

        native = NativeWatcher(FakeRecorder(tmp), {"zoom.us": [1111]}, set())
        check(native.detect_meeting() is None, "native app alone does not count as meeting")

        native.active.add(1111)
        detection = native.detect_meeting()
        check(detection is not None and detection.source == "zoom", "native app requires active mic input")

        cooldown = CooldownWatcher(FakeRecorder(tmp))
        candidate = DetectionResult(source="zoom", detail="Google Chrome: https://zoom.us/j/123")
        check(not cooldown._cooldown_elapsed(candidate, 10.0), "cooldown blocks first transient detection")
        check(not cooldown._cooldown_elapsed(candidate, 14.0), "cooldown still blocks before threshold")
        check(cooldown._cooldown_elapsed(candidate, 15.1), "cooldown allows stable meeting detection")

        # --- hard-stop: ceiling and silence ---
        import time as _t

        # Ceiling not yet reached, silence inactive: no hard stop.
        hs = EchoboxWatcher(
            FakeRecorder(tmp),
            max_recording_seconds=3600.0,
            silence_timeout_seconds=600.0,
            start_cooldown=0,
        )
        hs.recorder.start()
        hs._recording_started_at = 100.0
        hs.recorder._last_sound_at = 150.0
        check(hs._hard_stop_reason(200.0) is None, "no hard stop when within both bounds")
        check(hs._hard_stop_reason(3700.0) == "ceiling", "ceiling trips at >= max_recording_seconds")

        # Silence: last_sound_at is stale beyond silence_timeout_seconds.
        hs._recording_started_at = 500.0
        hs.recorder._last_sound_at = 500.0
        check(hs._hard_stop_reason(1100.0) == "silence", "silence trips at >= silence_timeout_seconds")
        check(hs._hard_stop_reason(1000.0) is None, "silence does NOT trip just below threshold")

        # Ceiling + detection present: rotates session (stops + starts a new one),
        # runs pipeline in background; recorder should still be active afterwards.
        pipeline_calls: list[Path] = []

        class RotatingWatcher(EchoboxWatcher):
            def __init__(self_inner, recorder_inner):
                super().__init__(
                    recorder_inner,
                    on_meeting_end=pipeline_calls.append,
                    max_recording_seconds=3600.0,
                    silence_timeout_seconds=600.0,
                    start_cooldown=0,
                )

        rot = RotatingWatcher(FakeRecorder(tmp))
        rot.recorder.start()
        rot._recording_started_at = 0.0  # force ceiling
        detection_live = DetectionResult(source="zoom", detail="X")
        handled = rot._handle_hard_stop("ceiling", detection_live)
        check(handled, "ceiling-with-detection handled")
        check(rot.recorder.active, "recorder re-armed after ceiling rotation")
        check(rot.recorder.stop_calls == 1, "rotation stopped the prior session exactly once")
        # Background pipeline thread may not have run yet — give it a moment.
        for _ in range(20):
            if pipeline_calls:
                break
            _t.sleep(0.05)
        check(len(pipeline_calls) == 1, "ceiling rotation ran pipeline in background")

        # Ceiling WITHOUT detection: stops synchronously, no restart.
        no_rot = RotatingWatcher(FakeRecorder(tmp))
        no_rot.recorder.start()
        no_rot._recording_started_at = 0.0
        no_rot._handle_hard_stop("ceiling", None)
        check(not no_rot.recorder.active, "ceiling without detection stops without restart")

        # Silence path always stops without restart, even if detection is present.
        sil = RotatingWatcher(FakeRecorder(tmp))
        sil.recorder.start()
        sil._handle_hard_stop("silence", detection_live)
        check(not sil.recorder.active, "silence stop never restarts")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
