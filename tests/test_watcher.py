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


class FakeRecorder(EchoboxRecorder):
    def __init__(self, output_dir: Path):
        super().__init__(output_dir, "demo-model")

    def start(self, session_hint: str = "call"):
        self._session = object()
        return self._session

    def stop(self):
        self._session = None
        return self.output_dir / "fake.txt"


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
                BROWSER_SCRIPTS["Google Chrome"]: "https://example.com\nhttps://meet.google.com/abc-defg-hij",
            },
        )
        detection = browser.detect_meeting()
        check(detection is not None and detection.source == "google-meet", "browser detection scans all tabs")
        check(
            detection is not None and "meet.google.com" in detection.detail,
            "browser detection returns matching background tab detail",
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
