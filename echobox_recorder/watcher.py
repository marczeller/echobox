from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .recorder import EchoboxRecorder, slugify_hint

MEETING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:$|[/?#])"), "google-meet"),
    (re.compile(r"^https://(?:[\w.-]+\.)?zoom\.us/(?:j|wc)/\d+(?:/[^?#]*)?(?:$|[?#])"), "zoom"),
    (re.compile(r"^https://teams\.microsoft\.com/(?:l/meetup-join|meet|v2/)(?:$|[/?#])"), "teams"),
    (re.compile(r"^https://app\.gather\.town/app/[^/?#]+/[^/?#]+(?:$|[/?#])"), "gather"),
    (re.compile(r"^https://whereby\.com/[^/?#]+(?:$|[/?#])"), "whereby"),
)

BROWSER_SCRIPTS = {
    "Google Chrome": '''
        tell application "Google Chrome"
            if (count of windows) is 0 then return ""
            set urls to {}
            repeat with w in every window
                repeat with t in every tab of w
                    set end of urls to (URL of t as text)
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            set out to urls as string
            set AppleScript's text item delimiters to ""
            return out
        end tell
    ''',
    "Safari": '''
        tell application "Safari"
            if (count of windows) is 0 then return ""
            set urls to {}
            repeat with w in every window
                repeat with t in every tab of w
                    set end of urls to (URL of t as text)
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            set out to urls as string
            set AppleScript's text item delimiters to ""
            return out
        end tell
    ''',
    "Arc": '''
        tell application "Arc"
            if (count of windows) is 0 then return ""
            set urls to {}
            repeat with w in every window
                repeat with t in every tab of w
                    set end of urls to (URL of t as text)
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            set out to urls as string
            set AppleScript's text item delimiters to ""
            return out
        end tell
    ''',
    "Firefox": '''
        tell application "Firefox"
            if (count of windows) is 0 then return ""
            set urls to {}
            repeat with w in every window
                repeat with t in every tab of w
                    set end of urls to (URL of t as text)
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            set out to urls as string
            set AppleScript's text item delimiters to ""
            return out
        end tell
    ''',
}

NATIVE_APPS = (
    ("zoom.us", "zoom"),
    ("Microsoft Teams", "teams"),
    ("FaceTime", "facetime"),
    ("Webex", "webex"),
)


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


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
        start_cooldown: float = 6.0,
        max_recording_seconds: float = 3600.0,
        silence_timeout_seconds: float = 600.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.recorder = recorder
        self.on_meeting_end = on_meeting_end or (lambda _path: None)
        self.poll_interval = poll_interval
        self.stop_grace_period = stop_grace_period
        self.start_cooldown = start_cooldown
        self.max_recording_seconds = float(max_recording_seconds)
        self.silence_timeout_seconds = float(silence_timeout_seconds)
        self.logger = logger or (lambda _message: None)
        self._last_seen_active = 0.0
        self._pending_detection: DetectionResult | None = None
        self._pending_since = 0.0
        self._recording_started_at = 0.0
        self.paused = False

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

    def _tab_urls(self, script: str) -> list[str]:
        output = self._run_osascript(script)
        if not output:
            return []
        return [line.strip() for line in re.split(r"[\r\n,]+", output) if line.strip()]

    @staticmethod
    def _safe_url_for_log(url: str) -> str:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return "<invalid-url>"
        if not parsed.scheme or not parsed.netloc:
            return "<invalid-url>"
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _match_meeting_url(self, browser: str, url: str) -> DetectionResult | None:
        lowered = url.strip().lower()
        for pattern, source in MEETING_PATTERNS:
            if pattern.search(lowered):
                safe_url = self._safe_url_for_log(url)
                return DetectionResult(source=source, detail=f"{browser}: {safe_url}")
        return None

    def _browser_has_meeting_tab(self) -> DetectionResult | None:
        for browser, script in BROWSER_SCRIPTS.items():
            for url in self._tab_urls(script):
                detection = self._match_meeting_url(browser, url)
                if detection is not None:
                    return detection
        return None

    def _pgrep_pids(self, app_name: str) -> list[int]:
        result = subprocess.run(
            ["pgrep", "-x", app_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        pids: list[int] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
        return pids

    def _coreaudio_lib(self):
        lib_path = ctypes.util.find_library("CoreAudio")
        if not lib_path:
            raise RuntimeError("CoreAudio framework not available")
        return ctypes.CDLL(lib_path)

    def _coreaudio_process_has_input(self, pid: int) -> bool:
        lib = self._coreaudio_lib()
        get_property = lib.AudioObjectGetPropertyData
        get_property.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(AudioObjectPropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        get_property.restype = ctypes.c_int32

        process_address = AudioObjectPropertyAddress(_fourcc("id2p"), _fourcc("glob"), 0)
        qualifier = ctypes.c_int(pid)
        process_object = ctypes.c_uint32(0)
        process_size = ctypes.c_uint32(ctypes.sizeof(process_object))
        status = get_property(
            1,
            ctypes.byref(process_address),
            ctypes.sizeof(qualifier),
            ctypes.byref(qualifier),
            ctypes.byref(process_size),
            ctypes.byref(process_object),
        )
        if status != 0 or process_object.value == 0:
            return False

        input_address = AudioObjectPropertyAddress(_fourcc("piri"), _fourcc("glob"), 0)
        input_running = ctypes.c_uint32(0)
        input_size = ctypes.c_uint32(ctypes.sizeof(input_running))
        status = get_property(
            process_object.value,
            ctypes.byref(input_address),
            0,
            None,
            ctypes.byref(input_size),
            ctypes.byref(input_running),
        )
        return status == 0 and bool(input_running.value)

    def _native_meeting_running(self) -> DetectionResult | None:
        for app_name, source in NATIVE_APPS:
            pids = self._pgrep_pids(app_name)
            if not pids:
                continue
            for pid in pids:
                try:
                    if self._coreaudio_process_has_input(pid):
                        return DetectionResult(source=source, detail=f"{app_name} pid={pid}")
                except Exception as exc:
                    self.logger(f"CoreAudio check failed for {app_name} ({pid}): {exc}")
                    break
        return None

    def detect_meeting(self) -> DetectionResult | None:
        return self._browser_has_meeting_tab() or self._native_meeting_running()

    def _same_detection(self, left: DetectionResult | None, right: DetectionResult | None) -> bool:
        # Compare only source type, not the full detail string which includes
        # all browser tab URLs and changes whenever any tab navigates.
        return bool(left and right and left.source == right.source)

    def _cooldown_elapsed(self, detection: DetectionResult, now: float) -> bool:
        if self.start_cooldown <= 0:
            return True
        if not self._same_detection(self._pending_detection, detection):
            self._pending_detection = detection
            self._pending_since = now
            return False
        return (now - self._pending_since) >= self.start_cooldown

    def _clear_pending_detection(self) -> None:
        self._pending_detection = None
        self._pending_since = 0.0

    def _start_recording(self, detection: DetectionResult) -> None:
        hint = slugify_hint(detection.source)
        self.recorder.start(hint)
        self._recording_started_at = time.monotonic()
        self.logger(f"Meeting detected via {detection.detail}")

    def _stop_recording(self) -> None:
        transcript_path = self.recorder.stop()
        self._recording_started_at = 0.0
        self.on_meeting_end(transcript_path)

    def _hard_stop_reason(self, now: float) -> str | None:
        """Return "ceiling" / "silence" / None.

        Ceiling guards against a tab left open indefinitely; the watcher
        restarts the session after the ceiling trips so a genuinely long
        call still gets chunked into reports rather than being abandoned.

        Silence guards against the same forgotten-tab case with a tighter
        bound: if both remote and local tracks have been below the peak
        threshold for `silence_timeout_seconds`, the call is effectively
        over even if the tab is still open.
        """
        if self.max_recording_seconds > 0 and self._recording_started_at > 0:
            if (now - self._recording_started_at) >= self.max_recording_seconds:
                return "ceiling"
        if self.silence_timeout_seconds > 0:
            last_sound = self.recorder.last_sound_monotonic()
            if last_sound > 0 and (now - last_sound) >= self.silence_timeout_seconds:
                return "silence"
        return None

    def _handle_hard_stop(self, reason: str, detection: DetectionResult | None) -> bool:
        """Run the appropriate stop path for a hard-stop reason.

        Returns True if caller should skip the remaining poll logic for
        this tick (i.e. recorder was stopped; new session may have been
        spawned for a ceiling restart).
        """
        if reason == "ceiling" and detection is not None:
            # Split a long call into chunks. `on_meeting_end` runs the
            # full transcribe+enrich+publish pipeline and can block for
            # several minutes; we must not let that gap swallow the
            # audio of the ongoing call, so the pipeline call is moved
            # to a background thread and recording resumes immediately.
            self.logger(
                f"Recording ceiling hit ({self.max_recording_seconds:.0f}s); "
                "rotating session and continuing in background"
            )
            try:
                transcript_path = self.recorder.stop()
            except Exception as exc:
                self.logger(f"Ceiling rotation stop failed: {exc}")
                self._recording_started_at = 0.0
                return True
            self._recording_started_at = 0.0
            threading.Thread(
                target=self._run_meeting_end_safely,
                args=(transcript_path,),
                name="echobox-rotate-pipeline",
                daemon=True,
            ).start()
            self._start_recording(detection)
            self._last_seen_active = time.monotonic()
            return True

        label = (
            f"silence >= {self.silence_timeout_seconds:.0f}s"
            if reason == "silence"
            else f"ceiling >= {self.max_recording_seconds:.0f}s"
        )
        self.logger(f"Force-stop: {label}")
        self._stop_recording()
        return True

    def _run_meeting_end_safely(self, transcript_path: Path) -> None:
        try:
            self.on_meeting_end(transcript_path)
        except Exception as exc:
            self.logger(f"Rotated-session pipeline failed: {exc}")

    def reset_activity_timer(self) -> None:
        self._last_seen_active = time.monotonic()

    def poll_once(self) -> None:
        if self.paused:
            return

        detection = self.detect_meeting()
        now = time.monotonic()

        if self.recorder.active:
            reason = self._hard_stop_reason(now)
            if reason is not None:
                if self._handle_hard_stop(reason, detection):
                    return

        if detection is not None:
            self._last_seen_active = now
            if self.recorder.active:
                self._clear_pending_detection()
            elif self._cooldown_elapsed(detection, now):
                self._start_recording(detection)
                self._clear_pending_detection()
        else:
            self._clear_pending_detection()
            if self.recorder.active and (now - self._last_seen_active) >= self.stop_grace_period:
                self._stop_recording()

    def run_forever(self) -> int:
        self.logger("Watcher ready")
        try:
            while True:
                try:
                    self.poll_once()
                except Exception as exc:
                    self.logger(f"Watcher poll failed: {exc}")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.logger("Watcher stopped")
            if self.recorder.active:
                self._stop_recording()
            return 0
