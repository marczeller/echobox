#!/usr/bin/env python3
"""Cover the PortAudio -9986 self-heal path in EchoboxRecorder._open_local_track.

Long-lived watcher processes hit `paInternalError (-9986)` after Bluetooth
reconnects because PortAudio's CoreAudio device cache drifts. The recorder
self-heals by calling `sd._terminate()` + `sd._initialize()` and retrying the
primary device once before falling back to the MacBook Pro mic. This file
asserts that contract end-to-end without needing a real audio device.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from echobox_recorder import EchoboxRecorder
from echobox_recorder import recorder as recorder_mod

PASS = 0
FAIL = 0


def check(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


class FakeStream:
    started = False
    closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeSD:
    """Minimal sounddevice surface for _open_local_track's self-heal logic."""

    def __init__(self) -> None:
        # Index 0 = AirPods (primary), index 1 = MacBook Pro Microphone (fallback)
        self._devices = [
            {
                "name": "AirPods Pro",
                "max_input_channels": 1,
                "default_samplerate": 24000,
            },
            {
                "name": "MacBook Pro Microphone",
                "max_input_channels": 1,
                "default_samplerate": 48000,
            },
        ]
        self.default = type("Default", (), {"device": [0, -1]})()
        self.terminate_calls = 0
        self.initialize_calls = 0

    def query_devices(self, idx=None):
        if idx is None:
            return list(self._devices)
        return self._devices[idx]

    def _terminate(self) -> None:
        self.terminate_calls += 1

    def _initialize(self) -> None:
        self.initialize_calls += 1


def _make_recorder(tmp: Path) -> EchoboxRecorder:
    return EchoboxRecorder(tmp, "demo-model", audio_dir=tmp)


def test_self_heal_recovers_on_refresh(tmp: Path) -> None:
    fake = FakeSD()
    original_import = recorder_mod._import_sounddevice
    recorder_mod._import_sounddevice = lambda: fake  # type: ignore[assignment]

    try:
        rec = _make_recorder(tmp)

        # All primary attempts raise -9986. After refresh, primary succeeds.
        attempts: list[tuple[int, int]] = []
        primary_attempts_before_refresh = 0

        def fake_create_local_stream(device, samplerate, channels):
            nonlocal primary_attempts_before_refresh
            attempts.append((device, samplerate))
            if device == 0 and fake.terminate_calls == 0:
                primary_attempts_before_refresh += 1
                raise OSError(
                    "Error opening InputStream: Internal PortAudio error "
                    "[PaErrorCode -9986]"
                )
            # After refresh OR on MBP fallback, succeed.
            return FakeStream()

        rec._create_local_stream = fake_create_local_stream  # type: ignore[assignment]

        result = rec._open_local_track("session-test")

        check(result is not None, "self-heal returned a stream tuple")
        if result is not None:
            stream, wav_handle, wav_path, sr, channels = result
            check(isinstance(stream, FakeStream), "returned stream is the post-refresh attempt")
            check(channels == 1, "channel count is 1")
            check(sr in (24000, 48000, 16000, 44100), "sample rate is from rate ladder")
            wav_handle.close()
            wav_path.unlink(missing_ok=True)

        check(fake.terminate_calls == 1, "PortAudio refresh triggered exactly once")
        check(fake.initialize_calls == 1, "PortAudio re-initialize called exactly once")
        # rate ladder for AirPods primary: reported(24000), 48000, 16000, 44100 = 4 attempts
        check(
            primary_attempts_before_refresh == 4,
            f"all 4 primary rates attempted before refresh (got {primary_attempts_before_refresh})",
        )
        # First post-refresh attempt should be the primary at the reported rate.
        check(
            attempts[4] == (0, 24000),
            f"refresh retry uses primary at reported rate (got {attempts[4] if len(attempts) > 4 else 'n/a'})",
        )
    finally:
        recorder_mod._import_sounddevice = original_import  # type: ignore[assignment]


def test_falls_through_to_mbp_when_refresh_also_fails(tmp: Path) -> None:
    fake = FakeSD()
    original_import = recorder_mod._import_sounddevice
    recorder_mod._import_sounddevice = lambda: fake  # type: ignore[assignment]

    try:
        rec = _make_recorder(tmp)
        attempts: list[tuple[int, int]] = []

        def fake_create_local_stream(device, samplerate, channels):
            attempts.append((device, samplerate))
            if device == 0:
                raise OSError(
                    "Error opening InputStream: Internal PortAudio error "
                    "[PaErrorCode -9986]"
                )
            return FakeStream()  # MBP fallback succeeds

        rec._create_local_stream = fake_create_local_stream  # type: ignore[assignment]

        result = rec._open_local_track("session-fallback")

        check(result is not None, "fallback returned a stream tuple")
        if result is not None:
            stream, wav_handle, wav_path, sr, _ = result
            wav_handle.close()
            wav_path.unlink(missing_ok=True)
            check(sr == 48000, "fallback uses MBP at 48kHz")

        check(fake.terminate_calls == 1, "refresh attempted exactly once even though it didn't help")
        # Final attempt landed on MBP (device 1)
        check(attempts[-1][0] == 1, "final attempt is MBP fallback (device 1)")
    finally:
        recorder_mod._import_sounddevice = original_import  # type: ignore[assignment]


def test_non_internal_error_skips_refresh(tmp: Path) -> None:
    """A non -9986 error on primary should NOT trigger a PortAudio refresh."""
    fake = FakeSD()
    original_import = recorder_mod._import_sounddevice
    recorder_mod._import_sounddevice = lambda: fake  # type: ignore[assignment]

    try:
        rec = _make_recorder(tmp)

        def fake_create_local_stream(device, samplerate, channels):
            if device == 0:
                # Different PortAudio error code: -9997 (paInvalidSampleRate)
                raise OSError("Error opening InputStream: PaErrorCode -9997")
            return FakeStream()

        rec._create_local_stream = fake_create_local_stream  # type: ignore[assignment]

        result = rec._open_local_track("session-nointernal")

        check(result is not None, "got fallback even without refresh")
        if result is not None:
            _, wav_handle, wav_path, _, _ = result
            wav_handle.close()
            wav_path.unlink(missing_ok=True)

        check(
            fake.terminate_calls == 0,
            f"refresh NOT triggered for non -9986 errors (got {fake.terminate_calls})",
        )
    finally:
        recorder_mod._import_sounddevice = original_import  # type: ignore[assignment]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="echobox-pa-test-") as raw:
        tmp = Path(raw)
        test_self_heal_recovers_on_refresh(tmp)
        test_falls_through_to_mbp_when_refresh_also_fails(tmp)
        test_non_internal_error_skips_refresh(tmp)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
