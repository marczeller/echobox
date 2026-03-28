#!/usr/bin/env python3
"""Print action items across enrichment sidecars.

Usage: python3 pipeline/actions.py <enrichment_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ACTION_RE = re.compile(r"^\s*[-*]\s+\*\*\[(?P<owner>[^\]]+)\]\*\*\s+(?P<task>.+?)\s*(?:\*\(by (?P<deadline>.+?)\)\*)?\s*$")


def parse_markdown_actions(markdown_path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    in_section = False

    for line in markdown_path.read_text(encoding="utf-8").splitlines():
        if line.strip().lower() == "## action items":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        match = ACTION_RE.match(line.strip())
        if not match:
            continue
        items.append(
            {
                "owner": match.group("owner").strip(),
                "task": match.group("task").strip(),
                "deadline": (match.group("deadline") or "").strip(),
            }
        )

    return items


def print_items(name: str, items: list[dict[str, str]]) -> int:
    print(f"  {name}")
    for item in items:
        owner = item.get("owner", "?")
        task = item.get("task", "")
        deadline = item.get("deadline", "")
        suffix = f" (by {deadline})" if deadline else ""
        print(f"    [{owner}] {task}{suffix}")
    print("")
    return len(items)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline/actions.py <enrichment_dir>")
        return 1
    enrichment_dir = Path(sys.argv[1])
    found = 0
    seen = set()

    print("Action Items Across All Calls")
    print("=============================")
    print("")

    for json_file in sorted(enrichment_dir.glob("*.json")):
        seen.add(json_file.stem)
        data = json.loads(json_file.read_text(encoding="utf-8"))
        items = data.get("action_items", [])
        if not items:
            continue
        name = json_file.stem.removesuffix("-enriched")
        found += print_items(name, items)

    for markdown_file in sorted(enrichment_dir.glob("*.md")):
        if markdown_file.stem in seen:
            continue
        items = parse_markdown_actions(markdown_file)
        if not items:
            continue
        name = markdown_file.stem.removesuffix("-enriched")
        found += print_items(name, items)

    if found == 0:
        print("  No action items found.")
        print("  Enrich some calls first: ./echobox.sh enrich <transcript>")
    else:
        print(f"  {found} action item(s) total")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
