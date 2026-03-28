#!/usr/bin/env python3
"""Smoke tests for the config parser flattening behavior."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    shim_dir = Path(tempfile.mkdtemp(prefix="echobox-yaml-shim-"))
    (shim_dir / "yaml.py").write_text(
        "import json\n\ndef safe_load(stream):\n    return json.loads(stream.read())\n"
    )
    sys.path.insert(0, str(shim_dir))

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.enrich import load_config

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
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        handle.write(
            """
{
  "whisper_model": "large-v3",
  "mlx_model": "demo-model",
  "mlx_url": "http://localhost:8090/v1/chat/completions",
  "workstation_ssh": "",
  "transcript_dir": "~/echobox-data/transcripts",
  "enrichment_dir": "~/echobox-data/enrichments",
  "report_dir": "~/echobox-data/reports",
  "log_dir": "~/echobox-data/logs",
  "prompt": {"template": ""},
  "context_sources": {
    "calendar": {"enabled": true, "command": "calendar {date}", "command_args": ["calendar", "--date", "{date}"]},
    "messages": {"enabled": false, "type": "sqlite", "path": "", "query": "SELECT * FROM messages"},
    "documents": {"enabled": false, "command": ""},
    "web": {"enabled": true}
  },
  "meeting_types": {
    "client_call": {"patterns": ["client", "partner"], "context": ["calendar", "messages", "documents", "web"]},
    "team_sync": {"patterns": ["sync", "weekly"], "internal_only": true, "context": ["documents"]},
    "general": {"patterns": [], "context": ["calendar", "web"]}
  },
  "publish": {"platform": "local", "password": "change-me", "scope": ""},
  "notify": {"enabled": false, "command": ""},
  "team": {"members": {"alex@example.com": "Alex"}, "internal_domains": ["company.com"], "roles": {"Alex": "Lead"}}
}
"""
        )
        handle.flush()
        config_path = Path(handle.name)

    config = load_config(config_path)
    config_path.unlink(missing_ok=True)

    print(f"Config loaded: {len(config)} keys")
    check(len(config) >= 25, f"expected >= 25 keys, got {len(config)}")

    check("whisper_model" in config, "top-level key: whisper_model")
    check("mlx_model" in config, "top-level key: mlx_model")
    check("mlx_url" in config, "top-level key: mlx_url")
    check("workstation_ssh" in config, "top-level key: workstation_ssh")

    check("context_sources.calendar.enabled" in config, "nested: context_sources.calendar.enabled")
    check("context_sources.calendar.command" in config, "nested: context_sources.calendar.command")
    check("context_sources.calendar.command_args.0" in config, "list items are indexed for command_args")
    check("context_sources.messages.enabled" in config, "nested: context_sources.messages.enabled")
    check("context_sources.messages.type" in config, "nested: context_sources.messages.type")
    check("context_sources.messages.path" in config, "nested: context_sources.messages.path")
    check("context_sources.messages.query" in config, "nested: context_sources.messages.query")
    check("context_sources.documents.enabled" in config, "nested: context_sources.documents.enabled")
    check("context_sources.web.enabled" in config, "nested: context_sources.web.enabled")

    check("meeting_types.client_call.patterns" in config, "nested: meeting_types.client_call.patterns")
    check("meeting_types.client_call.context" in config, "nested: meeting_types.client_call.context")
    check("meeting_types.team_sync.patterns" in config, "nested: meeting_types.team_sync.patterns")
    check("meeting_types.team_sync.internal_only" in config, "nested: meeting_types.team_sync.internal_only")
    check("meeting_types.general.patterns" in config, "nested: meeting_types.general.patterns")

    check("publish.platform" in config, "nested: publish.platform")
    check("publish.password" in config, "nested: publish.password")
    check("notify.enabled" in config, "nested: notify.enabled")
    check("notify.command" in config, "nested: notify.command")

    check(config["meeting_types.client_call.context"] == "calendar,messages,documents,web", "list flattened to comma-separated string")
    check(config["context_sources.calendar.command_args.2"] == "{date}", "command_args placeholder survives flattening")
    check(config["meeting_types.team_sync.internal_only"] == "true", "boolean normalized to true")
    check(config["team.roles.Alex"] == "Lead", "team role flattened correctly")

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
