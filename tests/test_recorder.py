#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from echobox_recorder import EchoboxRecorder
from echobox_recorder import EchoboxWatcher
from echobox_recorder.recorder import preferred_input_device

PASS = 0
FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


class FakeSoundDevice:
    default = type("Default", (), {"device": [2, 3]})()

    @staticmethod
    def query_devices():
        return [
            {"name": "MacBook Microphone", "max_input_channels": 1},
            {"name": "BlackHole 2ch", "max_input_channels": 2},
        ]


class FakeStream:
    def start(self):
        return None

    def stop(self):
        return None

    def close(self):
        return None


class TestRecorder(EchoboxRecorder):
    def resolve_input_device(self, sd_module=None):
        return 1

    def _create_stream(self, device):
        return FakeStream()

    def _transcribe_wav(self, wav_path: Path):
        return {"segments": [{"start": 0, "text": "hello world"}], "text": "hello world"}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="echobox-recorder-"))
    try:
        check(EchoboxRecorder is not None and EchoboxWatcher is not None, "package imports")
        check(preferred_input_device(FakeSoundDevice) == 1, "BlackHole is preferred when present")

        recorder = TestRecorder(tmp, "demo-model")
        recorder.start("roadmap")
        recorder._chunks = [b"\x00\x00" * 1600]
        transcript = recorder.stop()
        wav_path = transcript.with_suffix(".wav")
        check(transcript.exists(), "transcript is written")
        check(wav_path.exists(), "wav is retained")
        check("[00:00] [Unknown]: hello world" in transcript.read_text(encoding="utf-8"), "transcript contains formatted segment")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
