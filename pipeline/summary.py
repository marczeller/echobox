#!/usr/bin/env python3
"""Print a time-bounded summary across enrichment files.

Usage: python3 pipeline/summary.py <enrichment_dir> [N|--days N|--month|--all]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from echobox_types import EnrichmentSidecar  # noqa: E402


def parse_args(argv: list[str]) -> tuple[int, str]:
    days = 7
    label = "Weekly"
    if not argv:
        return days, label

    first = argv[0]
    if first == "--days":
        days = int(argv[1]) if len(argv) > 1 else 7
        label = f"Last {days}-day"
    elif first == "--month":
        days = 30
        label = "Monthly"
    elif first == "--all":
        days = 36500
        label = "All-time"
    elif first.isdigit():
        days = int(first)
        label = f"Last {days}-day"
    return days, label


def since_date(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def parse_markdown_summary(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if line.startswith("## Meeting Summary"):
            for candidate in lines[index + 1:index + 3]:
                if candidate.strip():
                    return candidate[:80]
    return ""


def parse_markdown_section(lines: list[str], heading_pattern: str) -> list[str]:
    items: list[str] = []
    in_section = False
    for line in lines:
        if re.search(heading_pattern, line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^##", line):
            break
        if in_section and re.match(r"^[-*]", line):
            items.append(f"    {line}")
    return items


def parse_json_sidecar(sidecar_path: Path) -> tuple[str, list[str], list[str]]:
    data: EnrichmentSidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    summary = (data.get("summary") or "")[:80]
    decisions = [f"    - {item}" for item in data.get("decisions", []) if item]
    actions: list[str] = []

    for item in data.get("action_items", []):
        owner = item.get("owner", "?")
        task = item.get("task", "")
        deadline = item.get("deadline", "")
        suffix = f" (by {deadline})" if deadline else ""
        if task:
            actions.append(f"    - [{owner}] {task}{suffix}")

    return summary, decisions, actions


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline/summary.py <enrichment_dir> [N|--days N|--month|--all]")
        return 1

    enrichment_dir = Path(sys.argv[1])
    days, label = parse_args(sys.argv[2:])
    since = since_date(days)
    call_count = 0
    enriched_count = 0
    total_actions = 0
    all_decisions = []
    all_actions = []

    print(f"{label} Summary")
    print("==============")
    print("")

    for enrichment in sorted(enrichment_dir.glob("*.md"), reverse=True):
        name = enrichment.stem.removesuffix("-enriched")
        file_date = re.search(r"^(\d{4}-\d{2}-\d{2})", name)
        if file_date and file_date.group(1) < since:
            continue
        call_count += 1
        sidecar = enrichment.with_suffix(".json")
        if sidecar.exists():
            summary, decisions, actions = parse_json_sidecar(sidecar)
        else:
            lines = enrichment.read_text(encoding="utf-8").splitlines()
            summary = parse_markdown_summary(lines)
            decisions = parse_markdown_section(lines, r"^##.*decision|^##.*key decision")
            actions = parse_markdown_section(lines, r"^##.*action")
        if summary:
            enriched_count += 1
            print(f"  {name}")
            print(f"    {summary}")
            print("")
        all_decisions.extend(decisions)
        all_actions.extend(actions)
        total_actions += len(actions)

    if call_count == 0:
        print(f"  No calls in the past {days} days.")
        return 0

    print(f"  Stats: {call_count} call(s), {enriched_count} enriched, {total_actions} action item(s)")
    print("")

    if all_decisions:
        print(f"  Key Decisions ({label}):")
        print("\n".join(all_decisions))
        print("")

    if all_actions:
        print("  Outstanding Action Items:")
        print("\n".join(all_actions))
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
