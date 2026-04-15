from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import rumps

from .recorder import audio_routing_ok
from .watcher import EchoboxWatcher

try:
    from .caption_panel import CaptionPanel
except Exception:  # pragma: no cover - pyobjc import guard
    CaptionPanel = None  # type: ignore[assignment,misc]


REPO_DIR = Path(__file__).resolve().parent.parent


class EchoboxMenuBar(rumps.App):
    ICON_IDLE = "\u25cb"       # ○
    ICON_RECORDING = "\u25c9"  # ◉
    ICON_PAUSED = "\u23f8"     # ⏸

    def __init__(
        self,
        watcher: EchoboxWatcher,
        *,
        transcript_dir: Path,
        audio_dir: Path | None = None,
        report_dir: Path,
        voices_dir: Path | None = None,
        raw_retention_days: int = 7,
        mixed_retention_days: int = 0,
        sweep_interval_minutes: int = 60,
        on_quit: Callable[[], None] | None = None,
        enable_caption_panel: bool = False,
    ) -> None:
        super().__init__(self.ICON_IDLE, quit_button=None)
        self.watcher = watcher
        self.transcript_dir = transcript_dir
        self.audio_dir = audio_dir or transcript_dir
        self.report_dir = report_dir
        self.voices_dir = voices_dir or (REPO_DIR / "voices")
        self._on_quit = on_quit
        self._poll_lock = threading.Lock()
        self._housekeeping_lock = threading.Lock()

        self._raw_retention_days = max(0, int(raw_retention_days or 0))
        self._mixed_retention_days = max(0, int(mixed_retention_days or 0))
        sweep_minutes = max(1, int(sweep_interval_minutes or 60))
        self._housekeeping_tick_target = max(1, (sweep_minutes * 60) // 3)

        self._caption_panel: CaptionPanel | None = None
        if enable_caption_panel and CaptionPanel is not None:
            try:
                self._caption_panel = CaptionPanel()
            except Exception as exc:
                self.watcher.logger(f"Caption panel disabled: {exc}")
                self._caption_panel = None
        self._wire_caption_panel()

        self._status_item = rumps.MenuItem("Idle", callback=None)
        self._status_item.set_callback(None)
        self._disk_status_item = rumps.MenuItem("Audio: (scanning)", callback=None)
        self._disk_status_item.set_callback(None)
        self._routing_status_item = rumps.MenuItem(
            "Audio routing: OK", callback=self._open_audio_midi_setup
        )
        self._end_call_item = rumps.MenuItem("End Call", callback=self._end_call)
        self._end_call_item.set_callback(None)  # disabled until recording
        self._toggle_item = rumps.MenuItem("Pause", callback=self._toggle_pause)
        self._skip_item = rumps.MenuItem("Skip This Meeting", callback=self._skip_meeting)
        self._skip_item.set_callback(None)  # disabled until recording
        self._recents_menu = rumps.MenuItem("Recent Transcripts")
        self._reports_menu = rumps.MenuItem("Recent Reports")
        self._voices_menu = rumps.MenuItem("Voices")
        self._disk_menu = rumps.MenuItem("Disk")
        self._open_audio_item = rumps.MenuItem(
            "Open Audio Folder", callback=self._open_audio_dir
        )
        self._prune_audio_item = rumps.MenuItem(
            "Prune old audio now", callback=self._prune_audio_now
        )
        self._disk_menu.add(self._open_audio_item)
        self._disk_menu.add(self._prune_audio_item)
        self._open_transcripts = rumps.MenuItem(
            "Open Transcripts Folder", callback=self._open_transcript_dir
        )
        self._open_reports = rumps.MenuItem(
            "Open Reports Folder", callback=self._open_report_dir
        )
        self._quit_item = rumps.MenuItem("Quit Echobox", callback=self._quit)

        self.menu = [
            self._status_item,
            self._disk_status_item,
            self._routing_status_item,
            None,  # separator
            self._end_call_item,
            self._toggle_item,
            self._skip_item,
            None,
            self._recents_menu,
            self._reports_menu,
            self._voices_menu,
            self._disk_menu,
            self._open_transcripts,
            self._open_reports,
            None,
            self._quit_item,
        ]

        self._populate_recents()
        self._populate_reports()
        self._refresh_voices(clear=False)
        self._refresh_disk_status()
        self._refresh_routing_status()

        # Handle SIGTERM for clean shutdown (launchd sends this)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _wire_caption_panel(self) -> None:
        """If the recorder uses the swift_helper backend, route its event stream
        through the caption panel. Safe to call even when the panel is disabled."""
        backend = getattr(self.watcher.recorder, "_swift_backend", None)
        if backend is None:
            return

        def _on_event(_session: Any, event: dict[str, Any]) -> None:
            panel = self._caption_panel
            if panel is None:
                return
            kind = event.get("type")
            if kind == "started":
                panel.reset()
                panel.show()
            panel.handle_event(event)
            if kind == "stopped":
                panel.hide()

        backend.on_event = _on_event

    def _handle_signal(self, signum, frame) -> None:
        self._cleanup_recording()
        rumps.quit_application()

    def _cleanup_recording(self) -> None:
        if self.watcher.recorder.active:
            self.watcher.logger("Stopping active recording on shutdown...")
            try:
                transcript_path = self.watcher.recorder.stop()
                self.watcher.on_meeting_end(transcript_path)
            except Exception as exc:
                self.watcher.logger(f"Error during shutdown cleanup: {exc}")

    # --- Polling in background thread to avoid blocking AppKit ---

    _report_refresh_counter = 0
    _housekeeping_counter = 0

    @rumps.timer(3)
    def _tick(self, _sender) -> None:
        # _tick fires on the main AppKit thread — safe for UI updates.
        # Check if the background poll changed state and update UI accordingly.
        self._update_ui()
        if self._recording_just_ended:
            self._recording_just_ended = False
            self._refresh_recents()
            self._refresh_reports()
            self._refresh_disk_status()
        # Refresh slow-cadence items every ~30s.
        self._report_refresh_counter += 1
        if self._report_refresh_counter >= 10:
            self._report_refresh_counter = 0
            self._refresh_reports()
            self._refresh_disk_status()
            self._refresh_routing_status()
            self._refresh_voices()
        # Run the housekeeping sweep on its own longer cadence.
        self._housekeeping_counter += 1
        if self._housekeeping_counter >= self._housekeeping_tick_target:
            self._housekeeping_counter = 0
            self._kick_housekeeping()
        # Kick off next poll in background if not already running
        if self._poll_lock.locked():
            return
        was_active = self.watcher.recorder.active
        thread = threading.Thread(
            target=self._poll_background, args=(was_active,), daemon=True
        )
        thread.start()

    _recording_just_ended = False

    def _kick_housekeeping(self) -> None:
        if self._raw_retention_days <= 0 and self._mixed_retention_days <= 0:
            return
        if self._housekeeping_lock.locked():
            return
        thread = threading.Thread(
            target=self._run_housekeeping, daemon=True
        )
        thread.start()

    def _run_housekeeping(self) -> None:
        with self._housekeeping_lock:
            try:
                from pipeline.clean import prune_audio
            except Exception as exc:
                self.watcher.logger(f"Housekeeping import failed: {exc}")
                return
            active = self._active_audio_paths()
            legacy = []
            if self.transcript_dir != self.audio_dir:
                legacy.append(self.transcript_dir)
            try:
                deleted = prune_audio(
                    audio_dir=self.audio_dir,
                    legacy_dirs=legacy,
                    raw_retention_days=self._raw_retention_days,
                    mixed_retention_days=self._mixed_retention_days,
                    active_paths=active,
                    dry_run=False,
                    logger=self.watcher.logger,
                )
            except Exception as exc:
                self.watcher.logger(f"Housekeeping sweep failed: {exc}")
                return
            if deleted:
                self.watcher.logger(
                    f"Housekeeping sweep pruned {len(deleted)} audio files"
                )

    def _active_audio_paths(self) -> set[Path]:
        """Return paths of in-flight recording files that must not be deleted."""
        paths: set[Path] = set()
        session = getattr(self.watcher.recorder, "_session", None)
        if session is None:
            return paths
        for attr in ("wav_path", "temp_wav_path", "local_wav_path", "remote_wav_path"):
            value = getattr(session, attr, None)
            if value is not None:
                paths.add(Path(value))
        return paths

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
            # Snapshot the session before stop() clears _session so we can
            # delete backend-specific artifacts afterwards.
            snapshot = session
            try:
                self.watcher.recorder.stop()
            finally:
                if snapshot is not None:
                    self.watcher.recorder.discard_session_artifacts(snapshot)
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

    # --- Disk status ---

    def _refresh_disk_status(self) -> None:
        target = self.audio_dir if self.audio_dir.exists() else self.transcript_dir
        try:
            total = 0
            count = 0
            for path in target.glob("*.wav"):
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    continue
            # Also include legacy location when audio_dir is set distinctly
            if target != self.transcript_dir and self.transcript_dir.exists():
                for path in self.transcript_dir.glob("*.wav"):
                    try:
                        total += path.stat().st_size
                        count += 1
                    except OSError:
                        continue
        except OSError:
            self._disk_status_item.title = "Audio: (error)"
            return
        gb = total / (1024 ** 3)
        if gb >= 1.0:
            size_str = f"{gb:.1f} GB"
        else:
            size_str = f"{total / (1024 ** 2):.0f} MB"
        self._disk_status_item.title = f"Audio: {size_str} · {count} files"

    def _open_audio_dir(self, _sender) -> None:
        subprocess.Popen(["open", str(self.audio_dir)])

    def _prune_audio_now(self, _sender) -> None:
        if self._raw_retention_days <= 0 and self._mixed_retention_days <= 0:
            rumps.alert(
                title="Echobox",
                message="Retention is disabled. Set cleanup.raw_track_retention_days or cleanup.mixed_audio_retention_days in echobox.yaml.",
            )
            return
        try:
            from pipeline.clean import prune_audio
        except Exception as exc:
            rumps.alert(title="Echobox", message=f"Cleanup module missing: {exc}")
            return
        active = self._active_audio_paths()
        legacy = []
        if self.transcript_dir != self.audio_dir:
            legacy.append(self.transcript_dir)
        try:
            deleted = prune_audio(
                audio_dir=self.audio_dir,
                legacy_dirs=legacy,
                raw_retention_days=self._raw_retention_days,
                mixed_retention_days=self._mixed_retention_days,
                active_paths=active,
                dry_run=False,
                logger=self.watcher.logger,
            )
        except Exception as exc:
            rumps.alert(title="Echobox", message=f"Prune failed: {exc}")
            return
        self._refresh_disk_status()
        rumps.alert(
            title="Echobox",
            message=f"Pruned {len(deleted)} audio file(s).",
        )

    # --- Routing status (BlackHole health check) ---

    def _refresh_routing_status(self) -> None:
        ok, reason = audio_routing_ok()
        if ok:
            # Hide the item by setting an empty title when routing is fine.
            self._routing_status_item.title = ""
            self._routing_status_item.set_callback(None)
        else:
            self._routing_status_item.title = f"⚠ Audio routing: {reason}"
            self._routing_status_item.set_callback(self._open_audio_midi_setup)

    def _open_audio_midi_setup(self, _sender) -> None:
        subprocess.Popen(["open", "-a", "Audio MIDI Setup"])

    # --- Voices submenu ---

    def _refresh_voices(self, clear: bool = True) -> None:
        if clear:
            self._voices_menu.clear()
        enroll_item = rumps.MenuItem("Enroll new voice...", callback=self._enroll_voice)
        self._voices_menu.add(enroll_item)
        try:
            files = sorted(self.voices_dir.glob("*.json")) if self.voices_dir.exists() else []
        except OSError:
            files = []
        if not files:
            placeholder = rumps.MenuItem("(no voices enrolled)", callback=None)
            placeholder.set_callback(None)
            self._voices_menu.add(placeholder)
            return
        for json_path in files:
            slug = json_path.stem
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
                display = meta.get("name") or slug
            except Exception:
                display = slug
            item = rumps.MenuItem(
                f"{display} ({slug})",
                callback=self._make_delete_voice_callback(slug, display),
            )
            self._voices_menu.add(item)

    def _make_delete_voice_callback(self, slug: str, display_name: str):
        def _delete(_sender):
            response = rumps.alert(
                title="Echobox",
                message=f"Delete voice '{display_name}' ({slug})?",
                ok="Delete",
                cancel="Cancel",
            )
            if not response:
                return
            try:
                subprocess.run(
                    [sys.executable, str(REPO_DIR / "pipeline" / "speaker_id.py"), "delete", slug],
                    capture_output=True,
                    check=False,
                )
            except Exception as exc:
                rumps.alert(title="Echobox", message=f"Delete failed: {exc}")
                return
            self._refresh_voices()
        return _delete

    def _enroll_voice(self, _sender) -> None:
        # File picker
        try:
            picker = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose file with prompt "Pick a 30-60s clean WAV of one speaker" '
                    'of type {"wav", "public.audio"})',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except Exception as exc:
            rumps.alert(title="Echobox", message=f"File picker failed: {exc}")
            return
        if picker.returncode != 0:
            return  # user cancelled
        wav_path = picker.stdout.strip()
        if not wav_path:
            return
        slug_resp = rumps.Window(
            message="Slug (short id, e.g. 'andrey')",
            title="Enroll voice · slug",
            default_text="",
            ok="Next",
            cancel="Cancel",
            dimensions=(240, 24),
        ).run()
        if not slug_resp.clicked or not slug_resp.text.strip():
            return
        slug = slug_resp.text.strip().lower()
        name_resp = rumps.Window(
            message="Display name",
            title="Enroll voice · display name",
            default_text=slug.title(),
            ok="Enroll",
            cancel="Cancel",
            dimensions=(240, 24),
        ).run()
        if not name_resp.clicked or not name_resp.text.strip():
            return
        display_name = name_resp.text.strip()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_DIR / "pipeline" / "speaker_id.py"),
                    "enroll",
                    slug,
                    wav_path,
                    display_name,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except Exception as exc:
            rumps.alert(title="Echobox", message=f"Enrollment failed: {exc}")
            return
        if result.returncode != 0:
            rumps.alert(title="Echobox", message=f"Enrollment failed: {result.stderr.strip()}")
            return
        self._refresh_voices()
        rumps.alert(title="Echobox", message=f"Enrolled {display_name} ({slug}).")

    # --- Quit ---

    def _quit(self, _sender) -> None:
        self._cleanup_recording()
        if self._on_quit:
            self._on_quit()
        rumps.quit_application()
