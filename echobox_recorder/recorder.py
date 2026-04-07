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


class EchoboxRecorder:
    def __init__(
        self,
        output_dir: str | Path,
        whisper_model: str,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        audio_device: int | str | None = None,
        whisper_language: str | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.whisper_model = whisper_model
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_device = audio_device
        self.whisper_language = whisper_language
        self.logger = logger or (lambda _message: None)
        self._wav_lock = threading.Lock()
        self._active_wav_handle: wave.Wave_write | None = None
        self._session: RecordingSession | None = None

    @property
    def active(self) -> bool:
        return self._session is not None

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

    def _create_stream(self, device: int | str | None):
        sd = _import_sounddevice()
        return sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=device,
            callback=self._stream_callback,
        )

    def start(self, session_hint: str = "call") -> RecordingSession:
        if self._session is not None:
            raise RuntimeError("Recorder already active")

        started_at = datetime.now().astimezone()
        transcript_id = f"{started_at.strftime('%Y-%m-%d_%H-%M')}_{slugify_hint(session_hint)}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        wav_path = self.output_dir / f"{transcript_id}.wav"
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
            dir=self.output_dir,
        )
        os.close(temp_fd)
        temp_wav_path = Path(temp_path_raw)
        wav_handle = wave.open(str(temp_wav_path), "wb")
        wav_handle.setnchannels(self.channels)
        wav_handle.setsampwidth(2)
        wav_handle.setframerate(self.sample_rate)
        stream = self._create_stream(device)
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
        try:
            with self._wav_lock:
                self._active_wav_handle = wav_handle
            stream.start()
        except Exception:
            with self._wav_lock:
                self._active_wav_handle = None
            try:
                stream.close()
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
            raise
        self._session = session
        self.logger(
            f"Recording started: {transcript_id} (device={device if device is not None else 'default'})"
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

    def stop(self) -> Path:
        if self._session is None:
            raise RuntimeError("Recorder is not active")

        session = self._session
        try:
            try:
                session.stream.stop()
            finally:
                session.stream.close()
        finally:
            with self._wav_lock:
                self._active_wav_handle = None
                session.wav_handle.close()
 

        duration_seconds = max(
            1,
            int((datetime.now(timezone.utc) - session.started_at.astimezone(timezone.utc)).total_seconds()),
        )
        try:
            session.temp_wav_path.replace(session.wav_path)
            result = self._transcribe_wav(session.wav_path)
            if isinstance(result, dict):
                result["_wav_path"] = str(session.wav_path)
            transcript_body = self._format_transcript(session.started_at, duration_seconds, result)
            session.transcript_path.write_text(transcript_body, encoding="utf-8")
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
