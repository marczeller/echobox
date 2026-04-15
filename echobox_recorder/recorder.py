from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _import_sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - exercised via callers
        raise RuntimeError(
            "sounddevice is required for recording. Install it with: python3 -m pip install --user sounddevice"
        ) from exc
    return sd


def _import_mlx_whisper():
    try:
        import mlx_whisper
    except Exception as exc:  # pragma: no cover - exercised via callers
        raise RuntimeError(
            "mlx-whisper is required for live transcription. Install it with: python3 -m pip install --user mlx-whisper"
        ) from exc
    return mlx_whisper


def slugify_hint(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "call"


def preferred_input_device(sd_module: Any | None = None) -> int | str | None:
    """Return BlackHole if available, otherwise the system default input device.

    BlackHole captures system audio (the other person's voice on a call),
    whereas the default mic only captures the local speaker.
    """
    sd = sd_module or _import_sounddevice()
    try:
        devices = sd.query_devices()
        for index, device in enumerate(devices):
            if not isinstance(device, dict):
                continue
            name = str(device.get("name", "")).lower()
            if "blackhole" in name and device.get("max_input_channels", 0) > 0:
                return index
    except Exception:
        pass
    try:
        default = getattr(sd, "default", None)
        device_pair = getattr(default, "device", None) if default is not None else None
        if isinstance(device_pair, (list, tuple)) and device_pair:
            return device_pair[0]
    except Exception:
        pass
    return None


def preferred_local_mic_device(sd_module: Any | None = None) -> int | None:
    """Return the device index of the local user's microphone.

    macOS already tracks the user's active input device as the system default
    (``kAudioHardwarePropertyDefaultInputDevice`` — whatever is selected in
    System Settings > Sound > Input). It auto-updates when AirPods, USB mics,
    Bluetooth headphones, Continuity Camera, or audio interfaces connect, so
    trusting it removes the need for hardcoded product-name keywords.

    Resolution order:
      1. macOS system default input, if it is a usable physical input.
      2. First usable external input device (USB / BT / interface).
      3. MacBook Pro built-in mic as last resort.

    "Usable" excludes BlackHole (loopback) and aggregate devices (CoreAudio
    multi-device bundles are flaky with sample rates).
    """
    sd = sd_module or _import_sounddevice()
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    def _usable(device: Any) -> bool:
        if not isinstance(device, dict):
            return False
        if device.get("max_input_channels", 0) <= 0:
            return False
        name = str(device.get("name", "")).lower()
        if "blackhole" in name or "aggregate" in name:
            return False
        return True

    try:
        idx = sd.default.device[0]
        if idx is not None and int(idx) >= 0:
            idx_int = int(idx)
            if idx_int < len(devices) and _usable(devices[idx_int]):
                return idx_int
    except Exception:
        pass

    external: list[int] = []
    internal: list[int] = []
    for index, device in enumerate(devices):
        if not _usable(device):
            continue
        name = str(device.get("name", "")).lower()
        if "macbook" in name or "built-in" in name:
            internal.append(index)
        else:
            external.append(index)

    if external:
        return external[0]
    if internal:
        return internal[0]
    return None


def macbook_pro_mic_device(sd_module: Any | None = None) -> int | None:
    """Return the device index of the MacBook Pro built-in microphone, or None."""
    sd = sd_module or _import_sounddevice()
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        if device.get("max_input_channels", 0) <= 0:
            continue
        name = str(device.get("name", "")).lower()
        if "blackhole" in name:
            continue
        if "macbook" in name or "built-in" in name:
            return index
    return None


def current_output_device() -> str | None:
    """Return the lowercase name of the current system audio output device,
    or None if SwitchAudioSource isn't installed or the query fails."""
    sas = shutil.which("SwitchAudioSource")
    if not sas:
        return None
    try:
        result = subprocess.run(
            [sas, "-c", "-t", "output"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().lower() or None


def audio_routing_ok() -> tuple[bool, str]:
    """Read-only health check: is the system output routed through BlackHole
    (typically via a Multi-Output Device)?

    Returns (True, "") when routing looks correct, or (False, reason) when
    the current output device will bypass BlackHole — causing the recorder
    to capture silence on the remote track. Used by the menu bar to surface
    a visible warning without auto-switching the output.
    """
    current = current_output_device()
    if current is None:
        # SwitchAudioSource missing — we can't verify, so don't alarm.
        return True, ""
    if "multi-output" in current or "blackhole" in current:
        return True, ""
    return False, f"Output is {current!r} — BlackHole won't receive audio"


def ensure_output_routes_to_blackhole(logger: Callable[[str], None] | None = None) -> None:
    """If BlackHole is the input device, ensure system output routes audio through it.

    AirPods (or any direct output) bypass BlackHole entirely, producing silent
    recordings.  The fix is to switch output to a Multi-Output Device that
    includes both the user's speakers/headphones AND BlackHole.
    """
    log = logger or (lambda _: None)
    sas = shutil.which("SwitchAudioSource")
    if not sas:
        return  # can't check without SwitchAudioSource

    try:
        current = subprocess.run(
            [sas, "-c", "-t", "output"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip().lower()
    except Exception:
        return

    if "multi-output" in current:
        return  # already routing through Multi-Output Device

    # Check if a Multi-Output Device exists
    try:
        all_outputs = subprocess.run(
            [sas, "-a", "-t", "output"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip()
    except Exception:
        return

    for line in all_outputs.splitlines():
        if "multi-output" in line.strip().lower():
            target = line.strip()
            try:
                subprocess.run(
                    [sas, "-s", target, "-t", "output"],
                    capture_output=True, text=True, timeout=3, check=True,
                )
                log(f"Auto-switched output to '{target}' (was '{current}') to route audio through BlackHole")
            except Exception as exc:
                log(f"Failed to switch output to '{target}': {exc}")
            return

    log(f"WARNING: Output is '{current}' — BlackHole won't receive audio. "
        f"Create a Multi-Output Device (Audio MIDI Setup) with your headphones + BlackHole.")


@dataclass
class RecordingSession:
    transcript_id: str
    started_at: datetime
    wav_path: Path
    temp_wav_path: Path
    transcript_path: Path
    device: int | str | None
    stream: Any
    wav_handle: wave.Wave_write
    backend: str = "sounddevice"
    session_dir: Path | None = None
    swift_session: Any = None
    local_stream: Any = None
    local_wav_handle: wave.Wave_write | None = None
    local_wav_path: Path | None = None
    local_sample_rate: int | None = None
    local_channels: int | None = None
    remote_wav_path: Path | None = None


class EchoboxRecorder:
    def __init__(
        self,
        output_dir: str | Path,
        whisper_model: str,
        *,
        audio_dir: str | Path | None = None,
        sample_rate: int = 16_000,
        channels: int = 1,
        audio_device: int | str | None = None,
        whisper_language: str | None = None,
        logger: Callable[[str], None] | None = None,
        capture_backend: str = "sounddevice",
        sessions_root: str | Path | None = None,
        swift_helper_source: str = "default-input",
        swift_helper_device_name: str | None = None,
        swift_helper_live_transcript: bool = False,
        swift_helper_whisperkit_model: str = "openai_whisper-tiny",
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.audio_dir = (
            Path(audio_dir).expanduser() if audio_dir is not None else self.output_dir
        )
        self.whisper_model = whisper_model
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_device = audio_device
        self.whisper_language = whisper_language
        self.logger = logger or (lambda _message: None)
        self._wav_lock = threading.Lock()
        self._active_wav_handle: wave.Wave_write | None = None
        self._local_wav_lock = threading.Lock()
        self._active_local_wav_handle: wave.Wave_write | None = None
        self._session: RecordingSession | None = None
        self.capture_backend = capture_backend
        self.sessions_root = (
            Path(sessions_root).expanduser()
            if sessions_root
            else self.output_dir.parent / "sessions"
        )
        self._swift_backend: Any | None = None
        if capture_backend == "swift_helper":
            from .swift_helper import SwiftHelperBackend

            self._swift_backend = SwiftHelperBackend(
                sessions_root=self.sessions_root,
                source=swift_helper_source,
                sample_rate=sample_rate,
                channels=channels,
                device_name=swift_helper_device_name,
                live_transcript=swift_helper_live_transcript,
                whisperkit_model=swift_helper_whisperkit_model,
                logger=self.logger,
            )
        elif capture_backend != "sounddevice":
            raise ValueError(
                f"unknown capture_backend: {capture_backend!r} "
                "(expected 'sounddevice' or 'swift_helper')"
            )

    @property
    def active(self) -> bool:
        if self._session is None:
            return False
        if self._session.backend == "swift_helper":
            self._check_swift_health()
        return self._session is not None

    def _check_swift_health(self) -> None:
        """If the swift helper has died or stopped sending heartbeats, tear
        down the session gracefully so the watcher observes `active == False`.

        This runs the normal post-call pipeline on whatever WAV was captured
        before the death, so any partial audio is still transcribed and
        enriched. Idempotent: returns silently if the helper is healthy."""
        session = self._session
        if session is None or session.backend != "swift_helper":
            return
        backend = self._swift_backend
        if backend is None:
            return
        swift_session = session.swift_session
        helper_dead = False
        reason = ""
        if swift_session is not None and swift_session.stopped:
            helper_dead = True
            reason = "helper process exited"
        else:
            status = backend.check_health()
            if status is not None:
                helper_dead = True
                reason = status
        if not helper_dead:
            return
        self.logger(
            f"swift helper stopped unexpectedly ({reason}); finalising partial session"
        )
        try:
            self.stop()
        except Exception as exc:
            self.logger(f"swift helper auto-finalise failed: {exc}")
            self._session = None

    def resolve_input_device(self, sd_module: Any | None = None) -> int | str | None:
        sd = sd_module or _import_sounddevice()
        if self.audio_device in (None, ""):
            return preferred_input_device(sd)
        if isinstance(self.audio_device, int):
            return self.audio_device
        if str(self.audio_device).isdigit():
            return int(str(self.audio_device))

        target = str(self.audio_device).lower()
        for index, device in enumerate(sd.query_devices()):
            if not isinstance(device, dict):
                continue
            if target in str(device.get("name", "")).lower():
                return index
        return self.audio_device

    def _stream_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            self.logger(f"Recorder warning: {status}")
        with self._wav_lock:
            wav_handle = self._active_wav_handle
            if wav_handle is None:
                return
            wav_handle.writeframes(bytes(indata))

    def _local_stream_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            self.logger(f"Local mic warning: {status}")
        with self._local_wav_lock:
            wav_handle = self._active_local_wav_handle
            if wav_handle is None:
                return
            wav_handle.writeframes(bytes(indata))

    def _create_stream(self, device: int | str | None):
        sd = _import_sounddevice()
        return sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=device,
            callback=self._stream_callback,
        )

    def _create_local_stream(
        self, device: int, samplerate: int, channels: int
    ):
        sd = _import_sounddevice()
        return sd.RawInputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            device=device,
            callback=self._local_stream_callback,
        )

    def _open_local_track(
        self, transcript_id: str
    ) -> tuple[Any, wave.Wave_write, Path, int, int] | None:
        """Open a second InputStream on the local mic (AirPods, fallback MBP mic).

        Returns (stream, wav_handle, wav_path, samplerate, channels) on success,
        or None if no suitable local device is available / the stream can't open.
        The stream is created but NOT started — caller is responsible for .start().

        AirPods on macOS are notoriously inconsistent about reporting a
        ``default_samplerate`` that CoreAudio actually honors — immediately after
        a BT link-mode transition the HAL can advertise 24000 Hz but then reject
        it with ``paInvalidSampleRate`` (-9986). We try the reported rate first,
        then walk a ladder of well-known rates, and finally fall back to the
        MacBook Pro built-in mic before giving up.
        """
        sd = _import_sounddevice()
        try:
            primary_idx = preferred_local_mic_device(sd)
        except Exception as exc:
            self.logger(f"Local mic lookup failed: {exc}")
            return None
        if primary_idx is None:
            self.logger("No local mic device found; recording remote-only")
            return None

        primary_info: dict[str, Any] | None = None
        try:
            info = sd.query_devices(primary_idx)
            if isinstance(info, dict):
                primary_info = info
        except Exception as exc:
            self.logger(f"Local mic query failed for device {primary_idx}: {exc}")

        primary_name = (
            str(primary_info.get("name", "unknown"))
            if primary_info is not None
            else f"device {primary_idx}"
        )
        reported_rate = (
            int(primary_info.get("default_samplerate") or 0)
            if primary_info is not None
            else 0
        )

        rates: list[int] = []
        for r in (reported_rate, 48000, 16000, 44100):
            if r and r not in rates:
                rates.append(r)

        candidates: list[tuple[int, str, int]] = [
            (primary_idx, primary_name, r) for r in rates
        ]

        mbp_idx = macbook_pro_mic_device(sd)
        if mbp_idx is not None and mbp_idx != primary_idx:
            try:
                mbp_info = sd.query_devices(mbp_idx)
                mbp_name = (
                    str(mbp_info.get("name", "MacBook Pro Microphone"))
                    if isinstance(mbp_info, dict)
                    else "MacBook Pro Microphone"
                )
            except Exception:
                mbp_name = "MacBook Pro Microphone"
            candidates.append((mbp_idx, mbp_name, 48000))

        self.audio_dir.mkdir(parents=True, exist_ok=True)
        local_wav_path = self.audio_dir / f"{transcript_id}-local.wav"
        last_error: str = ""
        for dev_idx, dev_name, sr in candidates:
            try:
                wav_handle = wave.open(str(local_wav_path), "wb")
                wav_handle.setnchannels(1)
                wav_handle.setsampwidth(2)
                wav_handle.setframerate(sr)
            except Exception as exc:
                last_error = f"wav open {sr}Hz: {exc}"
                self.logger(f"Local WAV open failed at {sr}Hz: {exc}")
                try:
                    local_wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            try:
                stream = self._create_local_stream(dev_idx, sr, 1)
            except Exception as exc:
                last_error = f"stream open {dev_name}@{sr}Hz: {exc}"
                self.logger(
                    f"Local stream open failed for device {dev_idx} ({dev_name}) "
                    f"at {sr}Hz/1ch: {exc}"
                )
                try:
                    wav_handle.close()
                finally:
                    try:
                        local_wav_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue
            self.logger(
                f"Local mic track: {dev_name} (device={dev_idx}, {sr}Hz, 1ch)"
            )
            if dev_idx != primary_idx:
                fallback_msg = (
                    f"Echobox local mic fell back to {dev_name} "
                    f"(preferred {primary_name} refused)"
                )
                self.logger(fallback_msg)
                try:
                    subprocess.run(
                        [
                            "osascript",
                            "-e",
                            f'display notification "{fallback_msg}" with title "Echobox"',
                        ],
                        capture_output=True,
                        timeout=3,
                        check=False,
                    )
                except Exception:
                    pass
            return stream, wav_handle, local_wav_path, sr, 1

        self.logger(
            f"All local mic candidates failed; recording remote-only. "
            f"Last error: {last_error or 'unknown'}"
        )
        return None

    def start(self, session_hint: str = "call") -> RecordingSession:
        if self._session is not None:
            raise RuntimeError("Recorder already active")
        if self.capture_backend == "swift_helper":
            return self._start_swift(session_hint)
        return self._start_sounddevice(session_hint)

    def _start_sounddevice(self, session_hint: str) -> RecordingSession:
        started_at = datetime.now().astimezone()
        transcript_id = f"{started_at.strftime('%Y-%m-%d_%H-%M')}_{slugify_hint(session_hint)}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = self.audio_dir / f"{transcript_id}.wav"
        transcript_path = self.output_dir / f"{transcript_id}.txt"
        device = self.resolve_input_device()
        # If recording via BlackHole, ensure system output routes through it
        if device is not None:
            sd = _import_sounddevice()
            try:
                dev_info = sd.query_devices(device)
                if isinstance(dev_info, dict) and "blackhole" in str(dev_info.get("name", "")).lower():
                    ensure_output_routes_to_blackhole(self.logger)
            except Exception:
                pass
        temp_fd, temp_path_raw = tempfile.mkstemp(
            suffix=".wav",
            prefix=f"{transcript_id}-",
            dir=self.audio_dir,
        )
        os.close(temp_fd)
        temp_wav_path = Path(temp_path_raw)
        wav_handle = wave.open(str(temp_wav_path), "wb")
        wav_handle.setnchannels(self.channels)
        wav_handle.setsampwidth(2)
        wav_handle.setframerate(self.sample_rate)
        stream = self._create_stream(device)
        local_track = self._open_local_track(transcript_id)
        session = RecordingSession(
            transcript_id=transcript_id,
            started_at=started_at,
            wav_path=wav_path,
            temp_wav_path=temp_wav_path,
            transcript_path=transcript_path,
            device=device,
            stream=stream,
            wav_handle=wav_handle,
        )
        if local_track is not None:
            (
                session.local_stream,
                session.local_wav_handle,
                session.local_wav_path,
                session.local_sample_rate,
                session.local_channels,
            ) = local_track
        try:
            with self._wav_lock:
                self._active_wav_handle = wav_handle
            if session.local_wav_handle is not None:
                with self._local_wav_lock:
                    self._active_local_wav_handle = session.local_wav_handle
            stream.start()
            if session.local_stream is not None:
                try:
                    session.local_stream.start()
                except Exception as exc:
                    self.logger(
                        f"Local stream start failed: {exc}; continuing remote-only"
                    )
                    with self._local_wav_lock:
                        self._active_local_wav_handle = None
                    try:
                        session.local_stream.close()
                    except Exception:
                        pass
                    try:
                        if session.local_wav_handle is not None:
                            session.local_wav_handle.close()
                    except Exception:
                        pass
                    if session.local_wav_path is not None:
                        try:
                            session.local_wav_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                    session.local_stream = None
                    session.local_wav_handle = None
                    session.local_wav_path = None
        except Exception:
            with self._wav_lock:
                self._active_wav_handle = None
            with self._local_wav_lock:
                self._active_local_wav_handle = None
            try:
                stream.close()
            except Exception:
                pass
            if session.local_stream is not None:
                try:
                    session.local_stream.close()
                except Exception:
                    pass
            try:
                with self._wav_lock:
                    wav_handle.close()
            finally:
                try:
                    temp_wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
            if session.local_wav_handle is not None:
                try:
                    session.local_wav_handle.close()
                except Exception:
                    pass
            if session.local_wav_path is not None:
                try:
                    session.local_wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
            raise
        self._session = session
        self.logger(
            f"Recording started: {transcript_id} (device={device if device is not None else 'default'})"
        )
        return session

    def _start_swift(self, session_hint: str) -> RecordingSession:
        from .swift_helper import session_id_from_hint

        assert self._swift_backend is not None
        started_at = datetime.now().astimezone()
        transcript_id = session_id_from_hint(slugify_hint(session_hint), started_at)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = self.output_dir / f"{transcript_id}.txt"
        swift_session = self._swift_backend.start(
            session_id=transcript_id,
            transcript_path=transcript_path,
        )
        # The session WAV lives inside the session dir; surface it as wav_path
        # so the orchestrator/legacy code can find it via convention.
        wav_path = swift_session.wav_path
        session = RecordingSession(
            transcript_id=transcript_id,
            started_at=started_at,
            wav_path=wav_path,
            temp_wav_path=wav_path,
            transcript_path=transcript_path,
            device=f"swift:{self._swift_backend.source}",
            stream=None,
            wav_handle=None,  # type: ignore[arg-type]
            backend="swift_helper",
            session_dir=swift_session.session_dir,
            swift_session=swift_session,
        )
        self._session = session
        self.logger(
            f"Recording started (swift_helper): {transcript_id} "
            f"source={self._swift_backend.source}"
        )
        return session

    def _vad_filter_audio(self, wav_path: Path) -> tuple[Any, list[tuple[float, float, float]]] | None:
        """Use silero-vad to extract speech segments, returning filtered audio and time mapping.

        Returns (audio_array, mapping) where mapping is [(concat_offset, original_start, duration), ...].
        Returns None if VAD is unavailable or finds no speech.
        """
        try:
            import torch
            from silero_vad import load_silero_vad, get_speech_timestamps, read_audio
        except ImportError:
            self.logger("silero-vad not installed, skipping VAD preprocessing")
            return None

        try:
            vad_model = load_silero_vad()
            audio = read_audio(str(wav_path), sampling_rate=self.sample_rate)
            timestamps = get_speech_timestamps(audio, vad_model, sampling_rate=self.sample_rate)
            if not timestamps:
                self.logger("VAD: no speech detected")
                return None

            padding = int(0.5 * self.sample_rate)
            chunks = []
            mapping: list[tuple[float, float, float]] = []
            concat_offset = 0.0

            for ts in timestamps:
                start_sample = max(0, ts["start"] - padding)
                end_sample = min(len(audio), ts["end"] + padding)
                chunk = audio[start_sample:end_sample]
                original_start = start_sample / self.sample_rate
                duration = len(chunk) / self.sample_rate
                mapping.append((concat_offset, original_start, duration))
                chunks.append(chunk)
                concat_offset += duration

            filtered = torch.cat(chunks).numpy()
            self.logger(f"VAD: {len(timestamps)} speech segments, {concat_offset:.0f}s of {len(audio) / self.sample_rate:.0f}s total")
            return filtered, mapping
        except Exception as exc:
            self.logger(f"VAD preprocessing failed: {exc}")
            return None

    def _remap_timestamps(self, segments: list[dict[str, Any]], mapping: list[tuple[float, float, float]]) -> list[dict[str, Any]]:
        """Remap Whisper segment timestamps from concatenated audio back to original time."""
        remapped = []
        for segment in segments:
            if not isinstance(segment, dict):
                remapped.append(segment)
                continue
            seg = dict(segment)
            for key in ("start", "end"):
                t = float(seg.get(key, 0) or 0)
                seg[key] = self._remap_time(t, mapping)
            remapped.append(seg)
        return remapped

    @staticmethod
    def _remap_time(t: float, mapping: list[tuple[float, float, float]]) -> float:
        """Map a time from concatenated audio back to original audio time."""
        for concat_offset, original_start, duration in reversed(mapping):
            if t >= concat_offset:
                return original_start + (t - concat_offset)
        return t

    def discard_session_artifacts(self, session: RecordingSession) -> None:
        """Remove all on-disk artifacts produced for a session. Used by
        `Skip This Meeting`. Handles both capture backends."""
        for path in (session.transcript_path, session.wav_path, session.temp_wav_path):
            try:
                if path is not None:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                self.logger(f"discard: could not delete {path}: {exc}")
        if session.backend == "swift_helper" and session.session_dir is not None:
            import shutil as _shutil

            try:
                _shutil.rmtree(session.session_dir, ignore_errors=True)
            except Exception as exc:
                self.logger(f"discard: could not remove {session.session_dir}: {exc}")

    def _write_final_jsonl(self, session: RecordingSession, result: dict[str, Any]) -> None:
        """Write the post-call transcription result as one JSON object per segment
        to <session_dir>/transcript.final.jsonl. Used by the Phase 4 session-dir
        layout so enrichment can consume structured segments rather than the
        flat .txt projection."""
        import json

        if session.session_dir is None:
            return
        segments = result.get("segments") if isinstance(result, dict) else None
        if not isinstance(segments, list):
            return
        path = session.session_dir / "transcript.final.jsonl"
        try:
            with path.open("w", encoding="utf-8") as fp:
                fp.write(
                    json.dumps(
                        {
                            "type": "meta",
                            "session_id": session.transcript_id,
                            "started_at": session.started_at.isoformat(),
                            "wav_path": str(session.wav_path),
                            "language": result.get("language"),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                for seg in segments:
                    if not isinstance(seg, dict):
                        continue
                    payload = {
                        "type": "segment",
                        "start": float(seg.get("start", 0) or 0),
                        "end": float(seg.get("end", 0) or 0),
                        "text": str(seg.get("text", "")).strip(),
                        "speaker": str(seg.get("speaker", "")) or None,
                    }
                    fp.write(json.dumps(payload, sort_keys=True) + "\n")
        except OSError as exc:
            self.logger(f"transcript.final.jsonl write failed: {exc}")

    @staticmethod
    def _filter_hallucinations(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter common Whisper hallucination patterns from segments."""
        filtered: list[dict[str, Any]] = []

        # --- Pass 1: Consecutive repetition filter ---
        # If the same text appears 3+ times in a row, keep only the first.
        deduped: list[dict[str, Any]] = []
        run_text: str | None = None
        run_count = 0
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            if text == run_text:
                run_count += 1
                if run_count < 3:
                    deduped.append(seg)
            else:
                run_text = text
                run_count = 1
                deduped.append(seg)

        # --- Pass 2: Internal repetition filter ---
        # Drop segments where >80% of tokens are the same word.
        for seg in deduped:
            text = str(seg.get("text", "")).strip()
            tokens = text.split()
            if len(tokens) >= 3:
                from collections import Counter
                most_common_count = Counter(t.lower().strip("'\",.-!?") for t in tokens).most_common(1)[0][1]
                if most_common_count / len(tokens) > 0.8:
                    continue
            filtered.append(seg)

        # --- Pass 3: Sliding window dedup ---
        # Drop duplicate text within a 5-segment window.
        final: list[dict[str, Any]] = []
        window: list[str] = []
        for seg in filtered:
            text = str(seg.get("text", "")).strip()
            if text in window:
                continue
            final.append(seg)
            window.append(text)
            if len(window) > 5:
                window.pop(0)

        return final

    def _transcribe_wav(self, wav_path: Path) -> dict[str, Any]:
        mlx_whisper = _import_mlx_whisper()

        transcribe_kwargs: dict[str, Any] = {
            "path_or_hf_repo": self.whisper_model,
            "hallucination_silence_threshold": 2.0,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.5,
            "compression_ratio_threshold": 1.8,
        }
        if self.whisper_language is not None:
            transcribe_kwargs["language"] = self.whisper_language

        vad_result = self._vad_filter_audio(wav_path)
        if vad_result is not None:
            audio, mapping = vad_result
            result = mlx_whisper.transcribe(audio, **transcribe_kwargs)
            if isinstance(result, dict) and result.get("segments"):
                result["segments"] = self._remap_timestamps(result["segments"], mapping)
        else:
            result = mlx_whisper.transcribe(str(wav_path), **transcribe_kwargs)

        if isinstance(result, dict) and result.get("segments"):
            result["segments"] = self._filter_hallucinations(result["segments"])

        return result if isinstance(result, dict) else {"text": str(result), "segments": []}

    def _import_diarization_dependencies(self) -> tuple[Any, Any]:
        try:
            import torch
            from pyannote.audio import Pipeline
        except Exception as exc:
            raise RuntimeError(
                "pyannote.audio diarization is unavailable. Install pyannote.audio and torch."
            ) from exc
        return torch, Pipeline

    def _diarization_device(self, torch_module: Any):
        if hasattr(torch_module, "backends") and hasattr(torch_module.backends, "mps"):
            try:
                if torch_module.backends.mps.is_available():
                    return torch_module.device("mps")
            except Exception:
                pass
        return torch_module.device("cpu")

    def diarize(self, wav_path: Path, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not segments:
            return segments

        hf_token = os.environ.get("HF_TOKEN", "").strip()
        if not hf_token:
            self.logger("Diarization skipped: HF_TOKEN is not set; using [Unknown] speaker labels")
            return segments

        try:
            torch, pipeline_cls = self._import_diarization_dependencies()
            pipeline = pipeline_cls.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token,
            )
            pipeline.to(self._diarization_device(torch))
            raw_output = pipeline(str(wav_path))
            if hasattr(raw_output, 'speaker_diarization'):
                diarization = raw_output.speaker_diarization
            elif hasattr(raw_output, 'to_annotation'):
                diarization = raw_output.to_annotation()
            else:
                diarization = raw_output
        except Exception as exc:
            self.logger(f"Diarization unavailable for {wav_path.name}: {exc}")
            return segments

        turns: list[tuple[float, float, str]] = []
        for turn, _track, speaker in diarization.itertracks(yield_label=True):
            turns.append((float(turn.start), float(turn.end), str(speaker)))

        diarized_segments: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                diarized_segments.append(segment)
                continue
            start = float(segment.get("start", 0) or 0)
            end = float(segment.get("end", start) or start)
            if end <= start:
                end = start + 0.01
            speaker = "[Unknown]"
            best_overlap = 0.0
            for turn_start, turn_end, turn_speaker in turns:
                overlap = min(end, turn_end) - max(start, turn_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    speaker = turn_speaker
            updated_segment = dict(segment)
            updated_segment["speaker"] = speaker
            diarized_segments.append(updated_segment)

        try:
            from pipeline.speaker_id import identify_speakers
            turn_segments = [
                {"start": s, "end": e, "speaker": spk} for s, e, spk in turns
            ]
            mapping = identify_speakers(wav_path, turn_segments, logger=self.logger)
            if mapping:
                for seg in diarized_segments:
                    if not isinstance(seg, dict):
                        continue
                    spk = seg.get("speaker")
                    if isinstance(spk, str) and spk in mapping:
                        seg["speaker"] = mapping[spk]
        except Exception as exc:
            self.logger(f"Voice ID skipped: {exc}")

        return diarized_segments

    def _format_transcript(self, started_at: datetime, duration_seconds: int, result: dict[str, Any]) -> str:
        lines = [
            f"Date: {started_at.date().isoformat()}",
            f"Start: {started_at.strftime('%H:%M')}",
            f"Duration: {duration_seconds // 60}:{duration_seconds % 60:02d}",
            "",
        ]
        wav_path = Path(str(result.get("_wav_path") or ""))
        segments = self.diarize(wav_path, result.get("segments") or [])
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            start = float(segment.get("start", 0) or 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            text = str(segment.get("text", "")).strip()
            speaker = str(segment.get("speaker", "[Unknown]") or "[Unknown]")
            if text:
                lines.append(f"[{minutes:02d}:{seconds:02d}] {speaker}: {text}")
        if len(lines) == 4:
            text = str(result.get("text", "")).strip()
            if text:
                lines.append(text)
        return "\n".join(lines).strip() + "\n"

    def _mix_or_promote_tracks(self, session: RecordingSession) -> None:
        """Finalise the session WAV.

        If only the remote (BlackHole) track exists, rename the temp WAV to
        wav_path — same behaviour as the single-stream path.

        If a local (AirPods / mic) track also exists, mix both tracks into
        wav_path via ffmpeg `amix`. Keep the raw tracks alongside the mixed
        file as `<transcript_id>-remote.wav` and `<transcript_id>-local.wav`
        for debugging and for downstream per-track diarization.
        """
        remote_track_path = session.temp_wav_path
        local_track_path = session.local_wav_path

        has_local = (
            local_track_path is not None
            and local_track_path.exists()
            and local_track_path.stat().st_size > 44  # empty WAV header is 44 bytes
        )

        if not has_local:
            remote_track_path.replace(session.wav_path)
            return

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self.logger(
                "ffmpeg not found; keeping remote-only track and dropping local track"
            )
            remote_track_path.replace(session.wav_path)
            return

        remote_final = self.audio_dir / f"{session.transcript_id}-remote.wav"
        local_final = self.audio_dir / f"{session.transcript_id}-local.wav"
        try:
            remote_track_path.replace(remote_final)
        except Exception as exc:
            self.logger(f"Remote track rename failed: {exc}")
            remote_track_path.replace(session.wav_path)
            return
        if local_track_path != local_final:
            try:
                local_track_path.replace(local_final)
            except Exception as exc:
                self.logger(f"Local track rename failed: {exc}")
                remote_final.replace(session.wav_path)
                try:
                    local_track_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return

        session.remote_wav_path = remote_final
        session.local_wav_path = local_final

        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(remote_final),
            "-i",
            str(local_final),
            "-filter_complex",
            "[0:a]aresample=16000[a0];"
            "[1:a]aresample=16000[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0,"
            "dynaudnorm=f=200:g=5",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            str(session.wav_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            self.logger(
                f"Mixed dual-track WAV: remote={remote_final.name}, "
                f"local={local_final.name} -> {session.wav_path.name}"
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            self.logger(
                f"ffmpeg amix failed ({exc.returncode}): {stderr.strip()[-400:]}; "
                f"falling back to remote-only track"
            )
            try:
                shutil.copyfile(remote_final, session.wav_path)
            except Exception as copy_exc:
                self.logger(f"remote-only fallback copy failed: {copy_exc}")
                raise

    def stop(self) -> Path:
        if self._session is None:
            raise RuntimeError("Recorder is not active")

        session = self._session
        if session.backend == "swift_helper":
            assert self._swift_backend is not None
            try:
                self._swift_backend.stop()
            except Exception as exc:
                self.logger(f"Swift helper stop failed: {exc}")
        else:
            try:
                try:
                    session.stream.stop()
                finally:
                    session.stream.close()
                if session.local_stream is not None:
                    try:
                        session.local_stream.stop()
                    except Exception as exc:
                        self.logger(f"Local stream stop failed: {exc}")
                    finally:
                        try:
                            session.local_stream.close()
                        except Exception as exc:
                            self.logger(f"Local stream close failed: {exc}")
            finally:
                with self._wav_lock:
                    self._active_wav_handle = None
                    if session.wav_handle is not None:
                        session.wav_handle.close()
                with self._local_wav_lock:
                    self._active_local_wav_handle = None
                    if session.local_wav_handle is not None:
                        try:
                            session.local_wav_handle.close()
                        except Exception:
                            pass

        duration_seconds = max(
            1,
            int((datetime.now(timezone.utc) - session.started_at.astimezone(timezone.utc)).total_seconds()),
        )
        try:
            if session.backend == "sounddevice":
                self._mix_or_promote_tracks(session)
            result = self._transcribe_wav(session.wav_path)
            if isinstance(result, dict):
                result["_wav_path"] = str(session.wav_path)
            transcript_body = self._format_transcript(session.started_at, duration_seconds, result)
            session.transcript_path.write_text(transcript_body, encoding="utf-8")
            if session.backend == "swift_helper" and session.session_dir is not None:
                self._write_final_jsonl(session, result)
        except Exception as exc:
            self.logger(
                f"Recording finalization failed for {session.transcript_id}: {exc} "
                f"(wav={session.wav_path if session.wav_path.exists() else session.temp_wav_path})"
            )
            if session.wav_path.exists() or session.temp_wav_path.exists():
                try:
                    transcript_body = (
                        f"Date: {session.started_at.date().isoformat()}\n"
                        f"Start: {session.started_at.strftime('%H:%M')}\n"
                        f"Duration: {duration_seconds // 60}:{duration_seconds % 60:02d}\n\n"
                        f"[Transcription failed: {exc}]\n"
                    )
                    session.transcript_path.write_text(transcript_body, encoding="utf-8")
                except Exception as transcript_exc:
                    self.logger(
                        f"Transcript write failed for {session.transcript_id}: {transcript_exc}"
                    )
            raise
        finally:
            self._session = None
        self.logger(f"Recording finished: {session.transcript_path}")
        return session.transcript_path
