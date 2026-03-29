#!/usr/bin/env python3
"""List recent calls and their available artifacts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def report_slug_for_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
    return re.sub(r"-{2,}", "-", slug)


def markdown_summary(enrichment_path: Path) -> str:
    for line in enrichment_path.read_text(encoding="utf-8").splitlines():
        if re.search(r"^#|^##|^###|Summary|Overview|Key Points", line):
            return re.sub(r"^#*\s*", "", line)[:60]
    return ""


def transcript_summary(transcript_path: Path) -> str:
    for line in transcript_path.read_text(encoding="utf-8").splitlines()[:3]:
        if re.search(r"Date|Call|Meeting", line, re.IGNORECASE):
            return line[:60]
    return ""


def main() -> int:
    if len(sys.argv) < 4:
        print("Usage: python3 pipeline/list_calls.py <transcript_dir> <enrichment_dir> <report_dir>")
        return 1

    transcript_dir = Path(sys.argv[1]).expanduser()
    enrichment_dir = Path(sys.argv[2]).expanduser()
    report_dir = Path(sys.argv[3]).expanduser()
    transcripts = sorted(transcript_dir.glob("*.txt"), reverse=True)

    print("Recent Calls")
    print("============")
    print("")
    if not transcripts:
        print("  No calls recorded yet.")
        print("")
        print("  Get started:")
        print("    ./echobox watch        Start recording calls")
        print("    ./echobox demo         Try the pipeline on sample data")
        return 0

    for transcript in transcripts[:60]:
        base = transcript.stem
        enrichment = enrichment_dir / f"{base}-enriched.md"
        raw_enrichment = enrichment_dir / f"{base}-raw.md"
        sidecar = enrichment_dir / f"{base}-enriched.json"
        report = None
        for report_name in (f"{base}-enriched", f"{base}-raw"):
            candidate = report_dir / report_slug_for_name(report_name) / "report.html"
            if candidate.exists():
                report = candidate
                break

        if report is not None and enrichment.exists():
            status = "transcript + enrichment + report"
        elif enrichment.exists():
            status = "transcript + enrichment"
        elif raw_enrichment.exists():
            status = "transcript + raw (not enriched)"
        else:
            status = "transcript only"

        summary = ""
        sidecar_data: dict[str, object] = {}
        if sidecar.exists():
            sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
            summary = str(sidecar_data.get("summary") or "")[:60]
        elif enrichment.exists():
            summary = markdown_summary(enrichment)
        elif raw_enrichment.exists():
            summary = transcript_summary(raw_enrichment)
        if not summary:
            summary = transcript_summary(transcript)

        call_date = ""
        date_match = re.search(r"^(\d{4}-\d{2}-\d{2})", base)
        time_match = re.search(r"_(\d{2})-(\d{2})", base)
        if date_match:
            call_date = date_match.group(1)
            if time_match:
                call_date += f" {time_match.group(1)}:{time_match.group(2)}"

        print(f"  {base}")
        if call_date:
            print(f"    When:    {call_date}")
        print(f"    Status:  {status}")
        if summary:
            print(f"    About:   {summary}")
        if sidecar_data:
            metrics = [
                f"speakers={len(sidecar_data.get('speakers', []))}",
                f"actions={len(sidecar_data.get('action_items', []))}",
                f"decisions={len(sidecar_data.get('decisions', []))}",
            ]
            participants = sidecar_data.get("participants", [])
            if participants:
                metrics.append(f"participants={len(participants)}")
            print(f"    Metrics: {', '.join(metrics)}")
        print("")

    print(f"  {len(transcripts)} call(s) total")
    print("")
    print("  Commands:")
    print("    echobox open               Open latest report")
    print("    echobox search <term>      Search across calls")
    print("    echobox reprocess <name>   Re-enrich and re-publish a call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
