from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .recorder import EchoboxRecorder, slugify_hint

MEETING_PATTERNS = (
    ("meet.google.com", "google-meet"),
    ("zoom.us/j/", "zoom"),
    ("zoom.us/wc/", "zoom"),
    ("teams.microsoft.com", "teams"),
    ("app.gather.town", "gather"),
    ("whereby.com", "whereby"),
)

BROWSER_SCRIPTS = {
    "Google Chrome": 'tell application "Google Chrome" to if (count of windows) > 0 then return URL of active tab of front window',
    "Safari": 'tell application "Safari" to if (count of windows) > 0 then return URL of current tab of front window',
    "Arc": 'tell application "Arc" to if (count of windows) > 0 then return URL of active tab of front window',
    "Firefox": 'tell application "Firefox" to if (count of windows) > 0 then return URL of active tab of front window',
}

NATIVE_APPS = (
    ("zoom.us", "zoom"),
    ("Microsoft Teams", "teams"),
    ("FaceTime", "facetime"),
    ("Webex", "webex"),
)


@dataclass
class DetectionResult:
    source: str
    detail: str


class EchoboxWatcher:
    def __init__(
        self,
        recorder: EchoboxRecorder,
        *,
        on_meeting_end: Callable[[Path], None] | None = None,
        poll_interval: float = 3.0,
        stop_grace_period: float = 12.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.recorder = recorder
        self.on_meeting_end = on_meeting_end or (lambda _path: None)
        self.poll_interval = poll_interval
        self.stop_grace_period = stop_grace_period
        self.logger = logger or (lambda _message: None)
        self._last_seen_active = 0.0

    def _run_osascript(self, script: str) -> str:
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def _browser_has_meeting_tab(self) -> DetectionResult | None:
        for browser, script in BROWSER_SCRIPTS.items():
            url = self._run_osascript(script)
            if not url:
                continue
            lowered = url.lower()
            for needle, source in MEETING_PATTERNS:
                if needle in lowered:
                    return DetectionResult(source=source, detail=f"{browser}: {url}")
        return None

    def _native_meeting_running(self) -> DetectionResult | None:
        for app_name, source in NATIVE_APPS:
            result = subprocess.run(
                ["pgrep", "-x", app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return DetectionResult(source=source, detail=app_name)
        return None

    def detect_meeting(self) -> DetectionResult | None:
        return self._browser_has_meeting_tab() or self._native_meeting_running()

    def _start_recording(self, detection: DetectionResult) -> None:
        hint = slugify_hint(detection.source)
        self.recorder.start(hint)
        self.logger(f"Meeting detected via {detection.detail}")

    def _stop_recording(self) -> None:
        transcript_path = self.recorder.stop()
        self.on_meeting_end(transcript_path)

    def run_forever(self) -> int:
        self.logger("Watcher ready")
        try:
            while True:
                detection = self.detect_meeting()
                now = time.monotonic()

                if detection is not None:
                    self._last_seen_active = now
                    if not self.recorder.active:
                        self._start_recording(detection)
                elif self.recorder.active and (now - self._last_seen_active) >= self.stop_grace_period:
                    self._stop_recording()

                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.logger("Watcher stopped")
            if self.recorder.active:
                self._stop_recording()
            return 0
