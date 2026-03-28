#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.report_render import extract_stats, md_to_html, render_report, render_transcript

PASS = 0
FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    fixture = (Path(__file__).parent / "fixtures" / "2026-03-15_10-00_roadmap-sync-enriched.md").read_text()
    transcript_fixture = (Path(__file__).parent / "fixtures" / "2026-03-15_10-00_roadmap-sync.txt").read_text()
    sample = """# Review

## Action Items
- **[Alex]** Ship *polished* report with `owner-tags`
  - Include nested detail

| Speaker | Identified As | Confidence |
|---|---|---|
| SPEAKER_00 | Alex Chen | high |

```json
{"ok": true}
```
"""
    stats = extract_stats(fixture, transcript_fixture)
    html = md_to_html(sample)
    transcript_html = render_transcript("[00:00] SPEAKER_00: Opening context\n[00:08] SPEAKER_01: Reply\n\nNarrator line")
    report = render_report("<html>{{ENRICHMENT_CONTENT}}{{TRANSCRIPT_CONTENT}}{{STAT_CARDS}}</html>", fixture, transcript_fixture, "Roadmap")
    check(stats.participant_count == 2, f"participant count extracted: {stats.participant_count}")
    check(stats.action_item_count == 3, f"action item count extracted: {stats.action_item_count}")
    check(stats.meeting_duration == "2:34", f"meeting duration extracted: {stats.meeting_duration}")
    check("<h1>Review</h1>" in html, "headings render")
    check("<strong>[Alex]</strong>" not in html and "owner-tag" in html , "action owner tag rendered")
    check("<em>polished</em>" in html and "<code>owner-tags</code>" in html, "inline emphasis renders")
    check(html.count("<ul>") >= 2, "nested lists render")
    check("<table>" in html and "<td>Alex Chen</td>" in html, "tables render")
    check("<pre><code>{&quot;ok&quot;: true}</code></pre>" in html, "code blocks render")
    check("speaker-0" in transcript_html and "speaker-1" in transcript_html and "00:00" in transcript_html, "speaker transcript colors assigned")
    check("Participants" in report and "Action Items" in report and "Meeting Duration" in report, "stat cards included in report")
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
