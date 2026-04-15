#!/usr/bin/env python3
"""Read nested config values and resolve Echobox directories.

Usage:
  python3 read_config.py <config_path> <key> [default]
  python3 read_config.py value <config_path> <key> [default]
  python3 read_config.py paths <config_path>
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import load_config


def safe_load_config(config_path: Path) -> dict[str, str]:
    try:
        return load_config(config_path)
    except Exception:
        return {}


def read_value(config_path: Path, key: str, default: str = "") -> str:
    config = safe_load_config(config_path)
    return config.get(key, default)


def expand_path(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def resolve_paths(config_path: Path) -> dict[str, str]:
    config = safe_load_config(config_path)
    data_dir = expand_path(os.environ.get("ECHOBOX_DATA_DIR", str(Path.home() / "echobox-data")))
    report_dir = os.environ.get(
        "ECHOBOX_REPORT_DIR",
        expand_path(config.get("report_dir", str(Path(data_dir) / "reports"))),
    )
    paths = {
        "DATA_DIR": data_dir,
        "TRANSCRIPT_DIR": os.environ.get(
            "ECHOBOX_TRANSCRIPT_DIR",
            expand_path(config.get("transcript_dir", str(Path(data_dir) / "transcripts"))),
        ),
        "AUDIO_DIR": os.environ.get(
            "ECHOBOX_AUDIO_DIR",
            expand_path(config.get("audio_dir", str(Path(data_dir) / "audio"))),
        ),
        "ENRICHMENT_DIR": os.environ.get(
            "ECHOBOX_ENRICHMENT_DIR",
            expand_path(config.get("enrichment_dir", str(Path(data_dir) / "enrichments"))),
        ),
        "REPORT_DIR": report_dir,
        "LOG_DIR": os.environ.get(
            "ECHOBOX_LOG_DIR",
            expand_path(config.get("log_dir", str(Path(data_dir) / "logs"))),
        ),
        "STATE_DIR": expand_path(
            os.environ.get(
                "ECHOBOX_STATE_DIR",
                config.get("state_dir", str(Path(report_dir).parent)),
            )
        ),
    }
    return paths


def print_paths(config_path: Path) -> int:
    for key, value in resolve_paths(config_path).items():
        print(f"{key}={shlex.quote(value)}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("", end="")
        return 0

    if argv[1] == "paths":
        if len(argv) < 3:
            print("", end="")
            return 0
        return print_paths(Path(argv[2]))

    if argv[1] == "value":
        if len(argv) < 4:
            print("", end="")
            return 0
        config_path = Path(argv[2])
        key = argv[3]
        default = argv[4] if len(argv) > 4 else ""
        print(read_value(config_path, key, default), end="")
        return 0

    config_path = Path(argv[1])
    key = argv[2] if len(argv) > 2 else ""
    default = argv[3] if len(argv) > 3 else ""
    print(read_value(config_path, key, default), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
