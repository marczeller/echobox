#!/usr/bin/env python3
"""Print the parsed Echobox config grouped by section.

Usage: python3 pipeline/show_config.py config/echobox.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import ConfigError
from enrich import load_config


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/echobox.yaml")

    print("Echobox Config")
    print("==============")
    print("")

    if not config_path.exists():
        print(f"No config found: {config_path}")
        print("Run: cp config/echobox.example.yaml config/echobox.yaml")
        return 1

    print(f"Source: {config_path}")
    print("")

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 1
    warnings: list[str] = []
    sections: dict[str, list[tuple[str, str, str]]] = {}

    for key, value in sorted(config.items()):
        section = key.split(".", 1)[0] if "." in key else "_top"
        display_value = value[:70] + "..." if len(value) > 70 else value
        flag = ""

        if key == "publish.password" and value in ("change-me", ""):
            flag = "  ← change this!"
            warnings.append("publish.password is still the default")
        elif value == "" and key not in ("workstation_ssh", "publish.scope", "notify.command"):
            flag = "  ← not set"

        sections.setdefault(section, []).append((key, display_value, flag))

    for section in sorted(sections):
        name = section if section != "_top" else "General"
        print(f"  [{name}]")
        for key, value, flag in sections[section]:
            print(f"    {key}: {value}{flag}")
        print("")

    if warnings:
        print("  Warnings:")
        for warning in warnings:
            print(f"    - {warning}")
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
