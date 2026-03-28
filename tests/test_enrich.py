#!/usr/bin/env python3
"""Smoke tests for pipeline/enrich.py metadata parsing and enrichment logic."""
from __future__ import annotations

import json
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
from pipeline.enrich import (
    parse_transcript_metadata,
    prepare_transcript_for_prompt,
    timestamp_match,
    classify_call_type,
    load_meeting_types,
    map_attendees,
    build_attendees_block,
    build_prompt,
    fetch_context_by_type,
    get_config_list,
    load_prompt_template,
    render_prompt_template,
    _sanitize_context_term,
)

PASS = 0
FAIL = 0
FIXTURES = Path(__file__).parent / "fixtures"


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    transcript_path = FIXTURES / "2026-03-15_10-00_roadmap-sync.txt"
    transcript_text = transcript_path.read_text()

    meta = parse_transcript_metadata(transcript_path, transcript_text)
    check(meta["date"] == "2026-03-15", f"parsed date: {meta['date']}")
    check(meta["duration"] == "2:34", f"parsed duration: {meta['duration']}")
    undiarized = prepare_transcript_for_prompt("[00:00] [Unknown]: Hello")
    check("no usable diarization" in undiarized, "undiarized transcripts get prompt guidance")
    diarized = prepare_transcript_for_prompt("[00:00] SPEAKER_00: Hello")
    check("no usable diarization" not in diarized, "diarized transcripts stay unchanged")

    calendar = json.loads((FIXTURES / "sample-calendar.json").read_text())
    events = calendar["items"]
    check(len(events) == 1, f"fixture has 1 event: {len(events)}")

    matched = timestamp_match(events, "10:00")
    check(matched.get("summary") == "Q2 Roadmap Sync", f"matched event: {matched.get('summary')}")

    no_match = timestamp_match(events, "22:00")
    check(no_match == {}, "no match for distant time")

    config = {
        "meeting_types.client_call.patterns": '["client", "customer"]',
        "meeting_types.team_sync.patterns": '["roadmap", "sync"]',
        "meeting_types.team_sync.internal_only": "true",
    }
    meeting_types = load_meeting_types(config)
    check("client_call" in meeting_types, f"meeting types loaded: {list(meeting_types.keys())}")
    check("team_sync" in meeting_types, "team_sync type present")

    attendees = map_attendees(matched, {})
    check(len(attendees) == 2, f"attendee count: {len(attendees)}")
    names = [a["name"] for a in attendees]
    check("Alex Chen" in names, f"attendee name: {names}")

    classification = classify_call_type(matched, attendees, meeting_types, set())
    check(classification["meeting_type"] is not None, f"classified: {classification}")

    internal_domains = {"company.com"}
    external_attendees = [{"email": "alice@evilcompany.com", "name": "Alice"}]
    classification = classify_call_type({}, external_attendees, meeting_types, internal_domains)
    check(classification["meeting_type"] == "general", "suffix-matching does not treat evilcompany.com as internal")

    subdomain_attendees = [{"email": "bob@eng.company.com", "name": "Bob"}]
    classification = classify_call_type({}, subdomain_attendees, meeting_types, internal_domains)
    check(classification["meeting_type"] == "team_sync", "subdomains of internal domains still count as internal")

    block = build_attendees_block(attendees, {})
    check("Alex Chen" in block, "attendees block contains name")
    check("<known_attendees>" in block, "attendees block has XML tags")
    fallback_block = build_attendees_block([], {"Marc Zeller": "CEO"}, fallback_names=["Marc Zeller", "Chris Ahn"])
    check("Marc Zeller (CEO)" in fallback_block, "fallback attendees prefer configured roles")
    check("Chris Ahn (team member)" in fallback_block, "fallback attendees include team members without roles")

    command_config = {
        "context_sources.messages.enabled": "true",
        "context_sources.messages.type": "command",
        "context_sources.messages.command": "printf 'recent note for {term}'",
        "meeting_types.general.context": "[messages]",
    }
    context = fetch_context_by_type(command_config, "", {"meeting_type": "general"}, {}, attendees)
    check("<message_context" in context, "command-based message context is included")
    check("recent note for Alex Chen" in context, "message command receives attendee term")
    check(_sanitize_context_term("Alice\n; rm -rf /") == "Alice rm -rf", "context terms collapse whitespace and strip shell metacharacters")
    check(_sanitize_context_term("bob@example.com\n&& whoami", allow_at=True) == "bob@example.com whoami", "context term sanitizer preserves email characters but drops shell operators")
    argv_config = {
        "context_sources.calendar.command_args.0": "gws",
        "context_sources.calendar.command_args.1": "calendar",
        "context_sources.calendar.command_args.2": "events",
        "context_sources.calendar.command_args.3": "list",
        "context_sources.calendar.command_args.4": "--params",
        "context_sources.calendar.command_args.5": '{"calendarId":"primary","timeMin":"{date}T00:00:00Z"}',
    }
    calendar_args = get_config_list(argv_config, "context_sources.calendar.command_args")
    check(calendar_args[0] == "gws" and calendar_args[-1].startswith('{"calendarId"'), "indexed config lists reconstruct command args")

    sample_enrichment = (FIXTURES / "2026-03-15_10-00_roadmap-sync-enriched.md").read_text()
    check("## Meeting Summary" in sample_enrichment, "sample enrichment fixture has meeting summary")
    check("## Action Items" in sample_enrichment, "sample enrichment fixture has action items")

    default_template = load_prompt_template({})
    check("{{transcript}}" in default_template, "default prompt template contains transcript placeholder")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as handle:
        handle.write("Summary for {{meeting_type}}\n{{known_attendees}}\n{{transcript}}\n{{curated_context}}")
        handle.flush()
        template_path = handle.name

    custom_template = load_prompt_template({"prompt.template": template_path})
    rendered = build_prompt(
        transcript_text,
        block,
        classification,
        "<document_context>Roadmap</document_context>",
        template_text=custom_template,
    )
    check("Summary for" in rendered, "custom prompt template loaded from file")
    check("Roadmap" in rendered, "custom prompt injects curated context")
    check("Alex Chen" in rendered, "custom prompt injects attendees")

    try:
        render_prompt_template("{{unknown_placeholder}}", {"transcript": "x"})
        check(False, "unknown placeholder should raise")
    except ValueError:
        check(True, "unknown placeholder is rejected")

    Path(template_path).unlink(missing_ok=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
