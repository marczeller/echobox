from __future__ import annotations

import signal
import subprocess
import threading
from pathlib import Path
from typing import Callable

import rumps

from .watcher import EchoboxWatcher


class EchoboxMenuBar(rumps.App):
    ICON_IDLE = "\u25cb"       # ○
    ICON_RECORDING = "\u25c9"  # ◉
    ICON_PAUSED = "\u23f8"     # ⏸

    def __init__(
        self,
        watcher: EchoboxWatcher,
        *,
        transcript_dir: Path,
        report_dir: Path,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(self.ICON_IDLE, quit_button=None)
        self.watcher = watcher
        self.transcript_dir = transcript_dir
        self.report_dir = report_dir
        self._on_quit = on_quit
        self._poll_lock = threading.Lock()

        self._status_item = rumps.MenuItem("Idle", callback=None)
        self._status_item.set_callback(None)
        self._end_call_item = rumps.MenuItem("End Call", callback=self._end_call)
        self._end_call_item.set_callback(None)  # disabled until recording
        self._toggle_item = rumps.MenuItem("Pause", callback=self._toggle_pause)
        self._skip_item = rumps.MenuItem("Skip This Meeting", callback=self._skip_meeting)
        self._skip_item.set_callback(None)  # disabled until recording
        self._recents_menu = rumps.MenuItem("Recent Transcripts")
        self._reports_menu = rumps.MenuItem("Recent Reports")
        self._open_transcripts = rumps.MenuItem(
            "Open Transcripts Folder", callback=self._open_transcript_dir
        )
        self._open_reports = rumps.MenuItem(
            "Open Reports Folder", callback=self._open_report_dir
        )
        self._quit_item = rumps.MenuItem("Quit Echobox", callback=self._quit)

        self.menu = [
            self._status_item,
            None,  # separator
            self._end_call_item,
            self._toggle_item,
            self._skip_item,
            None,
            self._recents_menu,
            self._reports_menu,
            self._open_transcripts,
            self._open_reports,
            None,
            self._quit_item,
        ]

        self._populate_recents()
        self._populate_reports()

        # Handle SIGTERM for clean shutdown (launchd sends this)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        self._cleanup_recording()
        rumps.quit_app()

    def _cleanup_recording(self) -> None:
        if self.watcher.recorder.active:
            self.watcher.logger("Stopping active recording on shutdown...")
            try:
                transcript_path = self.watcher.recorder.stop()
                self.watcher.on_meeting_end(transcript_path)
            except Exception as exc:
                self.watcher.logger(f"Error during shutdown cleanup: {exc}")

    # --- Polling in background thread to avoid blocking AppKit ---

    @rumps.timer(3)
    def _tick(self, _sender) -> None:
        # _tick fires on the main AppKit thread — safe for UI updates.
        # Check if the background poll changed state and update UI accordingly.
        self._update_ui()
        if self._recording_just_ended:
            self._recording_just_ended = False
            self._refresh_recents()
            self._refresh_reports()
        # Kick off next poll in background if not already running
        if self._poll_lock.locked():
            return
        was_active = self.watcher.recorder.active
        thread = threading.Thread(
            target=self._poll_background, args=(was_active,), daemon=True
        )
        thread.start()

    _recording_just_ended = False

    def _poll_background(self, was_active: bool) -> None:
        with self._poll_lock:
            try:
                self.watcher.poll_once()
            except Exception as exc:
                self.watcher.logger(f"Poll error: {exc}")
            if was_active and not self.watcher.recorder.active:
                self._recording_just_ended = True

    # --- UI updates ---

    def _update_ui(self) -> None:
        if self.watcher.paused:
            self.title = self.ICON_PAUSED
            self._status_item.title = "Paused"
            self._toggle_item.title = "Resume"
            self._end_call_item.set_callback(None)
            self._skip_item.set_callback(None)
        elif self.watcher.recorder.active:
            session = self.watcher.recorder._session
            hint = session.transcript_id if session else "call"
            self.title = self.ICON_RECORDING
            self._status_item.title = f"Recording: {hint}"
            self._toggle_item.title = "Pause"
            self._end_call_item.set_callback(self._end_call)
            self._skip_item.set_callback(self._skip_meeting)
        else:
            self.title = self.ICON_IDLE
            self._status_item.title = "Idle"
            self._toggle_item.title = "Pause"
            self._end_call_item.set_callback(None)
            self._skip_item.set_callback(None)

    def _end_call(self, _sender) -> None:
        if not self.watcher.recorder.active:
            return
        self.watcher.logger("Recording ended manually")
        self.watcher._stop_recording()
        # Reset activity timer so a new detection for the same meeting
        # isn't immediately killed by a stale grace period timestamp
        self.watcher.reset_activity_timer()
        self._update_ui()
        self._refresh_recents()
        self._refresh_reports()

    def _toggle_pause(self, _sender) -> None:
        self.watcher.paused = not self.watcher.paused
        if not self.watcher.paused:
            # Reset activity timer on resume to prevent immediate stop
            # of an active recording due to stale timestamp
            self.watcher.reset_activity_timer()
        self.watcher.logger(
            "Watcher paused" if self.watcher.paused else "Watcher resumed"
        )
        self._update_ui()

    def _skip_meeting(self, _sender) -> None:
        if not self.watcher.recorder.active:
            return
        session = self.watcher.recorder._session
        self.watcher.logger(f"Skipping meeting: {session.transcript_id if session else 'unknown'}")
        try:
            transcript_path = self.watcher.recorder.stop()
            transcript_path.unlink(missing_ok=True)
            wav_path = transcript_path.with_suffix(".wav")
            wav_path.unlink(missing_ok=True)
        except Exception as exc:
            self.watcher.logger(f"Error skipping: {exc}")
        self._update_ui()

    # --- Folder actions ---

    def _open_transcript_dir(self, _sender) -> None:
        subprocess.Popen(["open", str(self.transcript_dir)])

    def _open_report_dir(self, _sender) -> None:
        subprocess.Popen(["open", str(self.report_dir)])

    # --- Recent items ---

    def _populate_recents(self) -> None:
        self._refresh_recents(clear=False)

    def _refresh_recents(self, clear: bool = True) -> None:
        if clear:
            self._recents_menu.clear()
        try:
            transcripts = sorted(
                self.transcript_dir.glob("*.txt"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:5]
        except OSError:
            transcripts = []

        if not transcripts:
            item = rumps.MenuItem("No transcripts yet", callback=None)
            item.set_callback(None)
            self._recents_menu.add(item)
            return

        for path in transcripts:
            name = path.stem
            item = rumps.MenuItem(name, callback=self._make_open_callback(path))
            self._recents_menu.add(item)

    def _populate_reports(self) -> None:
        self._refresh_reports(clear=False)

    def _refresh_reports(self, clear: bool = True) -> None:
        if clear:
            self._reports_menu.clear()
        try:
            reports = sorted(
                self.report_dir.glob("*/report.html"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:5]
        except OSError:
            reports = []

        if not reports:
            item = rumps.MenuItem("No reports yet", callback=None)
            item.set_callback(None)
            self._reports_menu.add(item)
            return

        for path in reports:
            name = path.parent.name
            item = rumps.MenuItem(name, callback=self._make_open_callback(path))
            self._reports_menu.add(item)

    def _make_open_callback(self, path: Path):
        def _open(_sender):
            subprocess.Popen(["open", str(path)])
        return _open

    # --- Quit ---

    def _quit(self, _sender) -> None:
        self._cleanup_recording()
        if self._on_quit:
            self._on_quit()
        rumps.quit_app()
