#!/usr/bin/env python3
"""Show data usage and optionally prune old artifacts."""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from subprocess import run


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("days", nargs="?")
    parser.add_argument("--older", dest="older")
    parser.add_argument("--prune", action="store_true")
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        raise SystemExit(f"Error: unknown clean option: {unknown[0]}")
    raw_days = args.older or args.days or "90"
    if not str(raw_days).isdigit():
        raise SystemExit("Error: clean expects a numeric day count")
    args.days = int(raw_days)
    return args


def file_count(directory: Path, pattern: str) -> int:
    return sum(1 for _ in directory.glob(pattern))


def old_files(directory: Path, pattern: str, cutoff_seconds: float) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [path for path in directory.glob(pattern) if path.is_file() and (path.stat().st_mtime < cutoff_seconds)]
    )


def old_report_dirs(report_dir: Path, cutoff_seconds: float) -> list[Path]:
    if not report_dir.exists():
        return []
    return sorted(
        [path for path in report_dir.iterdir() if path.is_dir() and path.stat().st_mtime < cutoff_seconds]
    )


def main() -> int:
    if len(sys.argv) < 6:
        print("Usage: python3 pipeline/clean.py <data_dir> <transcript_dir> <enrichment_dir> <report_dir> <log_dir> [args...]")
        return 1

    data_dir = Path(sys.argv[1]).expanduser()
    transcript_dir = Path(sys.argv[2]).expanduser()
    enrichment_dir = Path(sys.argv[3]).expanduser()
    report_dir = Path(sys.argv[4]).expanduser()
    log_dir = Path(sys.argv[5]).expanduser()
    args = parse_args(sys.argv[6:])
    cutoff = time.time() - (args.days * 86400)

    print("Echobox Data Usage")
    print("==================")
    print("")
    print(f"  Transcripts: {file_count(transcript_dir, '*.txt')}")
    print(f"  Enrichments: {file_count(enrichment_dir, '*.md')}")
    print(f"  Reports:     {file_count(report_dir, '*/report.html')}")
    if shutil.which("du"):
        result = run(["du", "-sh", str(data_dir)], capture_output=True, text=True, check=False)
        size_value = result.stdout.split("\t", 1)[0] if result.returncode == 0 else "0B"
    else:
        size_value = "0B"
    print(f"  Total size:  {size_value}")
    print(f"  Location:    {data_dir}")
    print("")

    old_transcripts = old_files(transcript_dir, "*.txt", cutoff)
    old_enrichments = old_files(enrichment_dir, "*.md", cutoff)
    old_reports = old_files(report_dir, "*/report.html", cutoff)
    old_count = len(old_transcripts) + len(old_enrichments) + len(old_reports)

    if old_count == 0:
        print(f"  No files older than {args.days} days.")
        return 0

    print(f"  Files older than {args.days} days: {old_count}")
    print("")

    if old_transcripts:
        print("  Old transcripts:")
        for path in old_transcripts:
            print(f"    {path.name}")

    print("")
    if args.prune:
        for path in old_transcripts + old_enrichments + old_reports:
            path.unlink(missing_ok=True)
        for directory in old_report_dirs(report_dir, cutoff):
            shutil.rmtree(directory, ignore_errors=True)
        for path in old_files(log_dir, "*.log", cutoff):
            path.unlink(missing_ok=True)
        print(f"  Pruned files older than {args.days} days.")
    else:
        print("  To delete old files, run:")
        print(f"    ./echobox clean --older {args.days} --prune")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
