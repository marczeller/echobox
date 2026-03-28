#!/usr/bin/env python3
"""Run the Echobox fixture demo and publish the sample report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import ConfigError
from enrich import build_attendees_block
from enrich import build_prompt
from enrich import classify_call_type
from enrich import load_config
from enrich import load_meeting_types
from enrich import load_team_config
from enrich import map_attendees
from enrich import parse_transcript_metadata
from enrich import timestamp_match


def report_slug_for_name(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() or ch == "-" else "-" for ch in name)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug


def try_open(path: Path) -> None:
    commands = [["open", str(path)], ["xdg-open", str(path)]]
    for command in commands:
        try:
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue


def main() -> int:
    if len(sys.argv) < 5:
        print("Usage: python3 pipeline/demo.py <echobox_dir> <config> <report_dir> <open_flag>")
        return 1
    echobox_dir = Path(sys.argv[1])
    config_path = Path(sys.argv[2])
    report_dir = Path(sys.argv[3]).expanduser()

    fixture_dir = echobox_dir / "tests" / "fixtures"
    transcript = fixture_dir / "2026-03-15_10-00_roadmap-sync.txt"
    calendar = fixture_dir / "sample-calendar.json"
    enrichment = fixture_dir / "2026-03-15_10-00_roadmap-sync-enriched.md"
    if not transcript.exists():
        print(f"Error: demo fixture not found: {transcript}")
        return 1
    if not enrichment.exists():
        print(f"Error: demo enrichment fixture not found: {enrichment}")
        return 1

    print("Echobox Demo — Pipeline Walkthrough")
    print("====================================")
    print("")
    transcript_text = transcript.read_text(encoding="utf-8")
    try:
        config = load_config(config_path) if config_path.exists() else {}
    except ConfigError as exc:
        print(f"Warning: {exc}")
        print("Proceeding with built-in defaults for demo mode.")
        config = {}
    events = json.loads(calendar.read_text(encoding="utf-8")).get("items", [])

    print("[1/5] Parsing transcript metadata...")
    meta = parse_transcript_metadata(transcript, transcript_text)
    print(f"      Date: {meta['date']}  Time: {meta['time']}  Duration: {meta['duration']}")

    print("\n[2/5] Matching to calendar event...")
    matched = timestamp_match(events, meta["time"] or "10:00")
    if matched:
        print(f"      Matched: {matched.get('summary', '?')}")
        attendees = [item.get("displayName", item.get("email")) for item in matched.get("attendees", [])]
        print(f"      Attendees: {', '.join(attendees)}")
    else:
        print("      No match (would use general classification)")
        matched = {}

    print("\n[3/5] Classifying meeting type...")
    known_emails, internal_domains, team_roles = load_team_config(config)
    attendee_list = map_attendees(matched, known_emails)
    meeting_types = load_meeting_types(config)
    classification = (
        classify_call_type(matched, attendee_list, meeting_types, internal_domains)
        if matched else {"meeting_type": "general", "matched_pattern": None}
    )
    print(f"      Type: {classification['meeting_type']}")
    if classification.get("matched_pattern"):
        print(f"      Pattern: {classification['matched_pattern']}")

    print("\n[4/5] Building enrichment prompt...")
    attendees_block = build_attendees_block(attendee_list, team_roles)
    prompt = build_prompt(transcript_text, attendees_block, classification, "")
    print(f"      Prompt length: {len(prompt)} characters")
    print(f"      Attendees in prompt: {len(attendee_list)}")

    print("\n[5/5] Enrichment would be sent to LLM server")
    print("      (skipped in demo mode — no MLX server needed)")

    print("\n" + "=" * 50)
    print("PROMPT PREVIEW (first 500 chars):")
    print("=" * 50)
    print(prompt[:500])
    print("...")

    print("\nDemo complete. To run a real enrichment:")
    print("  1. Start your MLX server (mlx_lm.server)")
    print("  2. Run: ./echobox.sh enrich tests/fixtures/2026-03-15_10-00_roadmap-sync.txt")

    print("")
    print("Publishing fixture report...")
    sys.stdout.flush()
    env = os.environ.copy()
    env["ECHOBOX_TRANSCRIPT_DIR"] = str(fixture_dir)
    env["ECHOBOX_REPORT_DIR"] = str(report_dir)
    subprocess.run(
        ["bash", str(echobox_dir / "pipeline" / "publish.sh"), str(enrichment)],
        check=True,
        env=env,
    )
    report_slug = report_slug_for_name(enrichment.stem)
    report_path = report_dir / report_slug / "report.html"
    print("")
    print(f"Opening demo report: {report_path}")
    if sys.argv[4] == "open":
        try_open(report_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
