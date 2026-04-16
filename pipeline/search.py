#!/usr/bin/env python3
"""Search transcript and enrichment files."""
from __future__ import annotations

import sys
from pathlib import Path


def scan(directory: Path, label: str, pattern: str, suffixes: tuple[str, ...]) -> int:
    found = 0
    if not directory.is_dir():
        return 0
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        pattern_lower = pattern.lower()
        matches = [(index, line) for index, line in enumerate(lines, start=1) if pattern_lower in line.lower()]
        if not matches:
            continue
        print(f"  [{label}] {path.name} ({len(matches)} matches)")
        for index, line in matches[:3]:
            print(f"    {index}:{line}")
        print("")
        found += 1
    return found


def main() -> int:
    if len(sys.argv) < 4:
        print("Usage: python3 pipeline/search.py <term> <enrichment_dir> <transcript_dir>")
        return 1

    term = sys.argv[1]
    enrichment_dir = Path(sys.argv[2])
    transcript_dir = Path(sys.argv[3])

    if not term:
        print("Usage: echobox search <term>")
        print("  Searches across all transcripts and enrichments.")
        return 1

    print(f"Searching for: {term}")
    print("")

    found = 0
    found += scan(enrichment_dir, "enrichments", term, (".md",))
    found += scan(transcript_dir, "transcripts", term, (".txt",))

    if found == 0:
        print("  No matches found.")
    else:
        print(f"  {found} file(s) matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
