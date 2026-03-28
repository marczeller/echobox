#!/usr/bin/env python3
"""Smoke tests for terminal markdown preview rendering."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.markdown_preview import render_markdown

PASS = 0
FAIL = 0


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def main():
    sample = """# Call Review

## Action Items
- **Alex** Ship the preview command by Friday

| Speaker | Identified As | Confidence |
|---|---|---|
| SPEAKER_00 | Alex Chen | high |

```text
raw payload
```
"""

    rendered = render_markdown(sample, width=72, use_ansi=False)
    plain = strip_ansi(rendered)

    check("CALL REVIEW" in plain, "top-level heading is emphasized")
    check("ACTION ITEMS" in plain, "second-level heading is emphasized")
    check("- **Alex** Ship the preview command by Friday" in plain, "bullet is preserved")
    check("Speaker" in plain and "Identified As" in plain, "table headers rendered")
    check("SPEAKER_00" in plain and "Alex Chen" in plain, "table rows rendered")
    check("> raw payload" in plain, "code fences are rendered as quoted lines")

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
