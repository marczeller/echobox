#!/usr/bin/env python3
"""Advisory machine probes for agent-assisted Echobox setup.

This script is intentionally conservative:
- It does not modify config files.
- It does not read message contents by default.
- Calendar inspection is opt-in via --with-calendar.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

MESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"
APP_DIRS = (Path("/Applications"), Path.home() / "Applications")
COMMON_PROJECT_DIRS = (
    Path.home() / "Code",
    Path.home() / "Projects",
    Path.home() / "Developer",
    Path.home() / "src",
    Path.home() / "work",
    Path.home() / "repos",
    Path.home() / "Documents",
)
COMMON_NOTE_DIRS = (
    Path.home() / "Obsidian",
    Path.home() / "Documents" / "Obsidian",
    Path.home() / "Notes",
    Path.home() / "Documents" / "Notes",
    Path.home() / "logseq",
)
PATTERN_HINTS = {
    "client_call": ("client", "customer", "prospect", "demo", "discovery", "renewal", "pilot"),
    "investor_update": ("investor", "board", "fund", "financing"),
    "team_sync": ("sync", "standup", "retro", "weekly", "planning", "staff", "all hands"),
    "one_on_one": ("1:1", "one on one", "check-in"),
}
STOPWORDS = {
    "the", "and", "with", "for", "from", "call", "meeting", "sync", "weekly", "team", "client",
}


def command_exists(name: str) -> str:
    return shutil.which(name) or ""


def app_exists(name: str) -> str:
    for root in APP_DIRS:
        candidate = root / f"{name}.app"
        if candidate.exists():
            return str(candidate)
    return ""


def run_command(argv: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def readable_sqlite(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            connection.close()
        return True
    except Exception:
        return False


def module_exists(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def detect_projects() -> list[str]:
    found: list[str] = []
    env_project = os.environ.get("PROJECT_DIR", "").strip()
    if env_project:
        found.append(env_project)

    for root in COMMON_PROJECT_DIRS:
        if not root.is_dir():
            continue
        if (root / ".git").exists():
            found.append(str(root))
            continue
        children = 0
        try:
            iterator = list(root.iterdir())
        except OSError:
            continue
        for child in iterator:
            if not child.is_dir():
                continue
            if (child / ".git").exists():
                found.append(str(child))
                children += 1
            if children >= 5:
                break
    return sorted(dict.fromkeys(found))


def detect_note_dirs() -> list[str]:
    found: list[str] = []
    for path in COMMON_NOTE_DIRS:
        if path.is_dir():
            found.append(str(path))
    obsidian_config = Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if obsidian_config.exists():
        found.append(str(obsidian_config))
    return sorted(dict.fromkeys(found))


def detect_blackhole() -> bool:
    code, output, _ = run_command(["system_profiler", "SPAudioDataType"], timeout=20)
    return code == 0 and "BlackHole" in output


def choose_calendar_probe() -> tuple[str, str]:
    if command_exists("gws"):
        return (
            "gws",
            "gws calendar events list --params "
            "'{\"calendarId\":\"primary\",\"timeMin\":\"{date}T00:00:00Z\",\"timeMax\":\"{date}T23:59:59Z\",\"singleEvents\":true}'",
        )
    if command_exists("gcalcli"):
        return "gcalcli", "gcalcli agenda '{date} 00:00' '{date} 23:59' --details all --tsv"
    if command_exists("icalBuddy"):
        return "icalBuddy", ""
    return "", ""


def build_messages_recommendation(db_path: Path) -> dict[str, object]:
    query = (
        "SELECT "
        "COALESCE(handle.id, 'unknown') AS sender_name, "
        "datetime((message.date / 1000000000) + 978307200, 'unixepoch', 'localtime') AS ts, "
        "substr(COALESCE(message.text, ''), 1, 200) AS snippet "
        "FROM message "
        "LEFT JOIN handle ON message.handle_id = handle.ROWID "
        "WHERE COALESCE(message.text, '') LIKE '%{term}%' "
        "AND datetime((message.date / 1000000000) + 978307200, 'unixepoch') > datetime('now', '-90 days') "
        "ORDER BY message.date DESC "
        "LIMIT 15"
    )
    return {"enabled": True, "type": "sqlite", "path": str(db_path), "query": query}


def parse_event_items(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        if isinstance(payload.get("events"), list):
            return [item for item in payload["events"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def summarize_calendar_events(events: list[dict], days: int, errors: list[str]) -> dict[str, object]:
    summaries: list[str] = []
    attendee_domains: Counter[str] = Counter()
    examples: list[str] = []
    matched_types: Counter[str] = Counter()

    for event in events:
        summary = (event.get("summary") or event.get("title") or event.get("name") or "").strip()
        if summary:
            summaries.append(summary)
            if len(examples) < 8:
                examples.append(summary)
            lowered = summary.lower()
            for meeting_type, patterns in PATTERN_HINTS.items():
                if any(pattern in lowered for pattern in patterns):
                    matched_types[meeting_type] += 1

        for attendee in event.get("attendees", []):
            if not isinstance(attendee, dict):
                continue
            email = attendee.get("email", "")
            if "@" in email:
                attendee_domains[email.rsplit("@", 1)[1].lower()] += 1

    word_counts: Counter[str] = Counter()
    for summary in summaries:
        for token in re.findall(r"[a-zA-Z0-9:]+", summary.lower()):
            if len(token) < 3 or token in STOPWORDS:
                continue
            word_counts[token] += 1

    return {
        "enabled": True,
        "days": days,
        "event_count": len(events),
        "examples": examples,
        "top_words": [word for word, _count in word_counts.most_common(10)],
        "top_attendee_domains": [{"domain": domain, "count": count} for domain, count in attendee_domains.most_common(5)],
        "suggested_meeting_types": [{"name": name, "matches": count} for name, count in matched_types.most_common()],
        "errors": errors[:5],
    }


def run_calendar_probe(command_template: str, days: int) -> dict[str, object]:
    today = date.today()
    events: list[dict] = []
    errors: list[str] = []
    for offset in range(days):
        current = today - timedelta(days=offset)
        command = command_template.replace("{date}", current.isoformat())
        code, stdout, stderr = run_command(["zsh", "-lc", command], timeout=20)
        if code != 0:
            if stderr:
                errors.append(stderr.splitlines()[0])
            continue
        events.extend(parse_event_items(stdout))
    return summarize_calendar_events(events, days, errors)


def recommend_meeting_types(calendar_summary: dict[str, object] | None) -> list[dict[str, object]]:
    recommendations = [
        {"name": "client_call", "patterns": ["client", "customer", "prospect", "demo", "discovery"], "context": ["calendar", "messages", "documents", "web"]},
        {"name": "team_sync", "patterns": ["sync", "standup", "weekly", "retro", "planning"], "internal_only": True, "context": ["documents"]},
        {"name": "one_on_one", "patterns": ["1:1", "one on one", "check-in"], "context": ["calendar", "documents"]},
        {"name": "general", "patterns": [], "context": ["calendar", "web"]},
    ]
    if calendar_summary:
        suggested = {
            item["name"]: item["matches"]
            for item in calendar_summary.get("suggested_meeting_types", [])
            if isinstance(item, dict) and item.get("name")
        }
        if suggested.get("investor_update", 0) > 0:
            recommendations.insert(1, {"name": "investor_update", "patterns": ["investor", "board", "fund", "financing"], "context": ["calendar", "documents", "web"]})
    return recommendations


def gather_probes(with_calendar: bool, days: int) -> dict[str, object]:
    calendar_tool, calendar_command = choose_calendar_probe()
    probes = {
        "system": {"platform": sys.platform, "home": str(Path.home())},
        "commands": {
            "gws": command_exists("gws"),
            "gcalcli": command_exists("gcalcli"),
            "icalBuddy": command_exists("icalBuddy"),
            "ffmpeg": command_exists("ffmpeg"),
            "mdfind": command_exists("mdfind"),
            "sqlite3": command_exists("sqlite3"),
        },
        "modules": {
            "echobox_recorder": module_exists("echobox_recorder"),
            "sounddevice": module_exists("sounddevice"),
        },
        "apps": {"Slack": app_exists("Slack"), "Messages": app_exists("Messages"), "Obsidian": app_exists("Obsidian")},
        "blackhole_installed": detect_blackhole(),
        "messages": {"path": str(MESSAGE_DB), "exists": MESSAGE_DB.exists(), "readable": MESSAGE_DB.exists() and readable_sqlite(MESSAGE_DB)},
        "projects": detect_projects(),
        "notes": detect_note_dirs(),
        "env": {
            "PROJECT_DIR": os.environ.get("PROJECT_DIR", ""),
            "HF_TOKEN": "set" if os.environ.get("HF_TOKEN") else "",
            "ECHOBOX_WORKSTATION": os.environ.get("ECHOBOX_WORKSTATION", ""),
        },
        "calendar_probe": {"tool": calendar_tool, "command": calendar_command},
    }
    if with_calendar and calendar_command:
        probes["calendar_sample"] = run_calendar_probe(calendar_command, days)
    elif with_calendar and calendar_tool == "icalBuddy":
        probes["calendar_sample"] = {"enabled": False, "error": "icalBuddy is installed, but no JSON wrapper is configured."}
    return probes


def build_recommendations(probes: dict[str, object], calendar_summary: dict[str, object] | None) -> dict[str, object]:
    context_sources: dict[str, object] = {"web": {"enabled": True}}
    notes: list[str] = []
    cautions = [
        "Do not enable private data sources without user consent.",
        "Prefer drafting config changes for review instead of silently writing them.",
        "Messages.app and Calendar access may require Full Disk Access or Contacts/Calendar permissions.",
    ]

    calendar_probe = probes["calendar_probe"]
    if calendar_probe["command"]:
        context_sources["calendar"] = {"enabled": True, "command": calendar_probe["command"]}
        notes.append(f"Use `{calendar_probe['tool']}` for the calendar source.")
    elif calendar_probe["tool"] == "icalBuddy":
        notes.append("icalBuddy is installed, but it needs a wrapper that returns JSON with an items array.")
    else:
        notes.append("No reliable calendar CLI detected. Ask the user which calendar tool they use before configuring calendar context.")

    message_probe = probes["messages"]
    if message_probe["exists"] and message_probe["readable"]:
        context_sources["messages"] = build_messages_recommendation(Path(message_probe["path"]))
        notes.append("Messages.app chat history looks readable, so a local SQLite source is viable.")
    elif message_probe["exists"]:
        cautions.append("Messages chat.db exists but may be blocked by macOS privacy controls.")

    projects = probes["projects"]
    note_dirs = probes["notes"]
    if probes["commands"]["mdfind"]:
        context_sources["documents"] = {"enabled": True, "command": "mdfind '{term}' | head -5 | xargs -I{} head -20 '{}' 2>/dev/null"}
        notes.append("Spotlight (`mdfind`) is available, so document context can work across Notes, PDFs, and text files without extra setup.")
    else:
        document_root = os.environ.get("PROJECT_DIR", "").strip()
        if document_root:
            context_sources["documents"] = {"enabled": True, "command": "grep -rn '{term}' $PROJECT_DIR --include='*.md' --include='*.txt' -l | head -10 | xargs head -50"}
            notes.append("PROJECT_DIR is already set, so documents search can use it directly.")
        elif projects:
            context_sources["documents"] = {"enabled": True, "command": f"grep -rn '{{term}}' '{projects[0]}' --include='*.md' --include='*.txt' -l | head -10 | xargs head -50"}
            notes.append(f"Found likely project directories; start documents search from `{projects[0]}`.")
        elif note_dirs:
            context_sources["documents"] = {"enabled": True, "command": f"grep -rn '{{term}}' '{note_dirs[0]}' --include='*.md' --include='*.txt' -l | head -10 | xargs head -50"}
            notes.append(f"No project root detected, but notes directories exist; start documents search from `{note_dirs[0]}`.")

    return {
        "context_sources": context_sources,
        "meeting_types": recommend_meeting_types(calendar_summary),
        "questions": [
            "What kinds of calls do you actually take: investor, client, team, recruiting, support, or something else?",
            "Which calendar source do you trust most on this machine?",
            "Where do you keep project context: code repos, notes, docs, Slack exports, or Messages?",
            "Which sources are too private to include, even locally?",
        ],
        "notes": notes,
        "cautions": cautions,
    }


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text == "" or any(ch in text for ch in [":", "{", "}", "[", "]", "#", "'"]):
        return json.dumps(text)
    return text


def render_context_sources_yaml(context_sources: dict[str, object]) -> list[str]:
    lines = ["context_sources:"]
    for name, payload in context_sources.items():
        lines.append(f"  {name}:")
        for key, value in payload.items():
            if not isinstance(value, str):
                lines.append(f"    {key}: {yaml_scalar(value)}")
            elif "\n" not in value:
                lines.append(f"    {key}: {yaml_scalar(value)}")
            else:
                lines.append(f"    {key}: >")
                for piece in str(value).splitlines():
                    lines.append(f"      {piece}")
    return lines


def render_meeting_types_yaml(meeting_types: list[dict[str, object]]) -> list[str]:
    lines = ["meeting_types:"]
    for item in meeting_types:
        lines.append(f"  {item['name']}:")
        lines.append(f"    patterns: {json.dumps(item['patterns'])}")
        if item.get("internal_only"):
            lines.append("    internal_only: true")
        lines.append(f"    context: {json.dumps(item['context'])}")
    return lines


def render_markdown(report: dict[str, object]) -> str:
    probes = report["probes"]
    recommendations = report["recommendations"]
    lines = [
        "# Echobox Smart Setup Report",
        "",
        "This report is advisory. It does not change your machine or config.",
        "",
        "## Machine Probes",
        "",
        f"- Platform: `{probes['system']['platform']}`",
        f"- BlackHole detected: `{probes['blackhole_installed']}`",
        f"- `ffmpeg`: `{bool(probes['commands']['ffmpeg'])}`",
        f"- `echobox_recorder`: `{bool(probes['modules']['echobox_recorder'])}`",
        f"- `sounddevice`: `{bool(probes['modules']['sounddevice'])}`",
        f"- Calendar CLI: `{probes['calendar_probe']['tool'] or 'none detected'}`",
        f"- Slack.app: `{bool(probes['apps']['Slack'])}`",
        f"- Messages.app DB exists: `{probes['messages']['exists']}`",
        f"- Messages.app DB readable: `{probes['messages']['readable']}`",
    ]
    if probes["projects"]:
        lines.append(f"- Project roots: `{', '.join(probes['projects'][:3])}`")
    if probes["notes"]:
        lines.append(f"- Notes roots: `{', '.join(probes['notes'][:3])}`")

    calendar_sample = probes.get("calendar_sample")
    if isinstance(calendar_sample, dict):
        lines.extend(["", "## Calendar Sample", "", f"- Days sampled: `{calendar_sample.get('days', 0)}`", f"- Events seen: `{calendar_sample.get('event_count', 0)}`"])
        if calendar_sample.get("examples"):
            lines.append(f"- Example titles: `{'; '.join(calendar_sample['examples'][:5])}`")
        if calendar_sample.get("top_words"):
            lines.append(f"- Common title words: `{', '.join(calendar_sample['top_words'][:8])}`")

    lines.extend(["", "## Recommended Interview", ""])
    for question in recommendations["questions"]:
        lines.append(f"- {question}")

    lines.extend(["", "## Suggested Config", "", "```yaml", *render_context_sources_yaml(recommendations["context_sources"]), "", *render_meeting_types_yaml(recommendations["meeting_types"]), "```", "", "## Notes", ""])
    for note in recommendations["notes"]:
        lines.append(f"- {note}")
    lines.extend(["", "## Cautions", ""])
    for caution in recommendations["cautions"]:
        lines.append(f"- {caution}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe a macOS machine and draft Echobox setup suggestions.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--with-calendar", action="store_true", help="Probe recent calendar events using a detected calendar CLI.")
    parser.add_argument("--days", type=int, default=14, help="Number of days to sample when --with-calendar is set.")
    args = parser.parse_args(argv)

    probes = gather_probes(with_calendar=args.with_calendar, days=max(1, args.days))
    calendar_summary = probes.get("calendar_sample") if args.with_calendar else None
    report = {"probes": probes, "recommendations": build_recommendations(probes, calendar_summary if isinstance(calendar_summary, dict) else None)}

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
