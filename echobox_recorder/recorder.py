from __future__ import annotations

import re
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
    sd = sd_module or _import_sounddevice()
    default_input = None
    try:
        default = getattr(sd, "default", None)
        device_pair = getattr(default, "device", None) if default is not None else None
        if isinstance(device_pair, (list, tuple)) and device_pair:
            default_input = device_pair[0]
    except Exception:
        default_input = None

    try:
        devices = sd.query_devices()
    except Exception:
        return default_input

    for index, device in enumerate(devices):
        if (
            isinstance(device, dict)
            and "BlackHole" in str(device.get("name", ""))
            and int(device.get("max_input_channels", 0) or 0) > 0
        ):
            return index
    return default_input


@dataclass
class RecordingSession:
    transcript_id: str
    started_at: datetime
    wav_path: Path
    transcript_path: Path
    device: int | str | None
    stream: Any


class EchoboxRecorder:
    def __init__(
        self,
        output_dir: str | Path,
        whisper_model: str,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        audio_device: int | str | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.whisper_model = whisper_model
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_device = audio_device
        self.logger = logger or (lambda _message: None)
        self._chunks: list[bytes] = []
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
        self._chunks.append(bytes(indata))

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
        stream = self._create_stream(device)
        self._chunks = []
        self._session = RecordingSession(
            transcript_id=transcript_id,
            started_at=started_at,
            wav_path=wav_path,
            transcript_path=transcript_path,
            device=device,
            stream=stream,
        )
        stream.start()
        self.logger(
            f"Recording started: {transcript_id} (device={device if device is not None else 'default'})"
        )
        return self._session

    def _write_wav(self, wav_path: Path) -> None:
        with wave.open(str(wav_path), "wb") as handle:
            handle.setnchannels(self.channels)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            for chunk in self._chunks:
                handle.writeframes(chunk)

    def _transcribe_wav(self, wav_path: Path) -> dict[str, Any]:
        mlx_whisper = _import_mlx_whisper()
        result = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=self.whisper_model)
        return result if isinstance(result, dict) else {"text": str(result), "segments": []}

    def _format_transcript(self, started_at: datetime, duration_seconds: int, result: dict[str, Any]) -> str:
        lines = [
            f"Date: {started_at.date().isoformat()}",
            f"Start: {started_at.strftime('%H:%M')}",
            f"Duration: {duration_seconds // 60}:{duration_seconds % 60:02d}",
            "",
        ]
        segments = result.get("segments") or []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            start = float(segment.get("start", 0) or 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            text = str(segment.get("text", "")).strip()
            if text:
                lines.append(f"[{minutes:02d}:{seconds:02d}] [Unknown]: {text}")
        if len(lines) == 4:
            text = str(result.get("text", "")).strip()
            if text:
                lines.append(text)
        return "\n".join(lines).strip() + "\n"

    def stop(self) -> Path:
        if self._session is None:
            raise RuntimeError("Recorder is not active")

        session = self._session
        self._session = None
        session.stream.stop()
        session.stream.close()

        duration_seconds = max(
            1,
            int((datetime.now(timezone.utc) - session.started_at.astimezone(timezone.utc)).total_seconds()),
        )
        self._write_wav(session.wav_path)

        try:
            result = self._transcribe_wav(session.wav_path)
            transcript_body = self._format_transcript(session.started_at, duration_seconds, result)
        except Exception as exc:
            transcript_body = (
                f"Date: {session.started_at.date().isoformat()}\n"
                f"Start: {session.started_at.strftime('%H:%M')}\n"
                f"Duration: {duration_seconds // 60}:{duration_seconds % 60:02d}\n\n"
                f"[Transcription failed: {exc}]\n"
            )
            self.logger(f"Transcription failed for {session.wav_path.name}: {exc}")

        session.transcript_path.write_text(transcript_body, encoding="utf-8")
        self.logger(f"Recording finished: {session.transcript_path}")
        return session.transcript_path
