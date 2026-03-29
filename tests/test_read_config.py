#!/usr/bin/env python3
"""Smoke tests for resilient path resolution in pipeline/read_config.py."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline import read_config

PASS = 0
FAIL = 0


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    original = read_config.load_config
    temp_home = Path(tempfile.mkdtemp(prefix="echobox-read-config-"))
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(temp_home)

    try:
        def boom(_config_path: Path):
            raise ModuleNotFoundError("yaml")

        read_config.load_config = boom
        paths = read_config.resolve_paths(Path("/tmp/does-not-matter.yaml"))
        check(paths["TRANSCRIPT_DIR"] == str(temp_home / "echobox-data" / "transcripts"), "paths fall back to default transcript dir when config import fails")
        check(paths["REPORT_DIR"] == str(temp_home / "echobox-data" / "reports"), "paths fall back to default report dir when config import fails")
        value = read_config.read_value(Path("/tmp/does-not-matter.yaml"), "mlx_url", "fallback")
        check(value == "fallback", "read_value returns the provided default when config import fails")

        os.environ["ECHOBOX_DATA_DIR"] = "~/custom-echobox"
        os.environ["ECHOBOX_STATE_DIR"] = "$HOME/custom-state"
        env_paths = read_config.resolve_paths(Path("/tmp/does-not-matter.yaml"))
        check(env_paths["DATA_DIR"] == str(temp_home / "custom-echobox"), "ECHOBOX_DATA_DIR expands ~")
        check(env_paths["STATE_DIR"] == str(temp_home / "custom-state"), "ECHOBOX_STATE_DIR expands environment variables")
    finally:
        read_config.load_config = original
        os.environ.pop("ECHOBOX_DATA_DIR", None)
        os.environ.pop("ECHOBOX_STATE_DIR", None)
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
