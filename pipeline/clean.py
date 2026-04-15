#!/usr/bin/env python3
"""Show data usage and optionally prune old artifacts.

Also exposes ``prune_audio`` as a library function for the menu bar's
housekeeping tick — the same logic powers the CLI ``--audio`` flag and the
background sweep, so retention policy stays consistent between them.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from subprocess import run


FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
FILENAME_TIME_RE = re.compile(r"_(\d{2})-(\d{2})")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("days", nargs="?")
    parser.add_argument("--older", dest="older")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--audio", dest="audio", action="store_true",
                        help="also prune .wav files according to retention policy")
    parser.add_argument("--no-audio", dest="audio", action="store_false")
    parser.set_defaults(audio=False)
    parser.add_argument("--audio-raw-days", type=int, default=7,
                        help="retention for -local.wav / -remote.wav (default 7, 0 = off)")
    parser.add_argument("--audio-mixed-days", type=int, default=0,
                        help="retention for mixed <slug>.wav (default 0 = keep forever)")
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


def _wav_age_seconds(path: Path, now: float) -> float:
    """Return age in seconds, preferring the filename timestamp and falling
    back to filesystem mtime if the name doesn't carry one."""
    name = path.name
    date_match = FILENAME_DATE_RE.match(name)
    if date_match:
        time_match = FILENAME_TIME_RE.search(name)
        try:
            if time_match:
                stamp = datetime.strptime(
                    f"{date_match.group(1)}_{time_match.group(1)}-{time_match.group(2)}",
                    "%Y-%m-%d_%H-%M",
                )
            else:
                stamp = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            return max(0.0, now - stamp.timestamp())
        except ValueError:
            pass
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return 0.0


def _classify_wav(path: Path) -> str:
    """Return "raw" for dual-track artifacts, "mixed" for the final track."""
    name = path.name
    if name.endswith("-local.wav") or name.endswith("-remote.wav"):
        return "raw"
    return "mixed"


def prune_audio(
    audio_dir: Path,
    legacy_dirs: list[Path],
    raw_retention_days: int,
    mixed_retention_days: int,
    *,
    active_paths: set[Path] | None = None,
    dry_run: bool = False,
    logger=None,
) -> list[Path]:
    """Delete .wav files older than the retention policy.

    ``audio_dir`` is the canonical audio directory; ``legacy_dirs`` lists
    extra locations to sweep (typically ``transcript_dir`` so pre-upgrade
    .wav files still age out). ``active_paths`` is a set of paths the
    caller knows are currently held open (the in-flight recording) and
    must not be touched. When ``dry_run`` is True, no files are deleted
    and the caller gets the list of matches it would delete.
    """
    log = logger or (lambda _msg: None)
    skip = {Path(p).resolve() for p in (active_paths or set())}
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in [audio_dir, *legacy_dirs]:
        if not directory or not directory.exists():
            continue
        for path in directory.glob("*.wav"):
            try:
                real = path.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            candidates.append(path)

    now = time.time()
    deleted: list[Path] = []
    for path in candidates:
        try:
            if path.resolve() in skip:
                continue
        except OSError:
            continue
        classification = _classify_wav(path)
        retention_days = raw_retention_days if classification == "raw" else mixed_retention_days
        if retention_days <= 0:
            continue
        cutoff_seconds = retention_days * 86400
        if _wav_age_seconds(path, now) < cutoff_seconds:
            continue
        if dry_run:
            deleted.append(path)
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted.append(path)
            log(f"Pruned {classification} audio: {path.name} ({size / 1_048_576:.1f} MB)")
        except OSError as exc:
            log(f"Failed to delete {path}: {exc}")
    return deleted


def main() -> int:
    if len(sys.argv) < 6:
        print("Usage: python3 pipeline/clean.py <data_dir> <transcript_dir> <enrichment_dir> <report_dir> <log_dir> [audio_dir] [args...]")
        return 1

    data_dir = Path(sys.argv[1]).expanduser()
    transcript_dir = Path(sys.argv[2]).expanduser()
    enrichment_dir = Path(sys.argv[3]).expanduser()
    report_dir = Path(sys.argv[4]).expanduser()
    log_dir = Path(sys.argv[5]).expanduser()
    extra_args = sys.argv[6:]
    audio_dir = transcript_dir
    if extra_args and not extra_args[0].startswith("-") and not extra_args[0].isdigit():
        audio_dir = Path(extra_args.pop(0)).expanduser()
    args = parse_args(extra_args)
    cutoff = time.time() - (args.days * 86400)

    print("Echobox Data Usage")
    print("==================")
    print("")
    print(f"  Transcripts: {file_count(transcript_dir, '*.txt')}")
    print(f"  Audio:       {file_count(audio_dir, '*.wav')}")
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

    if args.audio:
        legacy = [transcript_dir] if transcript_dir != audio_dir else []
        old_audio = prune_audio(
            audio_dir=audio_dir,
            legacy_dirs=legacy,
            raw_retention_days=args.audio_raw_days,
            mixed_retention_days=args.audio_mixed_days,
            dry_run=True,
        )
    else:
        old_audio = []

    total = old_count + len(old_audio)

    if total == 0:
        print(f"  No files older than {args.days} days.")
        return 0

    print(f"  Files older than {args.days} days: {total}")
    print("")

    if old_transcripts:
        print("  Old transcripts:")
        for path in old_transcripts:
            print(f"    {path.name}")

    if old_audio:
        print(
            f"  Old audio (raw>{args.audio_raw_days}d, "
            f"mixed>{args.audio_mixed_days}d): {len(old_audio)}"
        )
        for path in old_audio[:20]:
            print(f"    {path.name}")
        if len(old_audio) > 20:
            print(f"    ... and {len(old_audio) - 20} more")

    print("")
    if args.prune:
        for path in old_transcripts + old_enrichments + old_reports:
            path.unlink(missing_ok=True)
        for directory in old_report_dirs(report_dir, cutoff):
            shutil.rmtree(directory, ignore_errors=True)
        for path in old_files(log_dir, "*.log", cutoff):
            path.unlink(missing_ok=True)
        if args.audio:
            legacy = [transcript_dir] if transcript_dir != audio_dir else []
            deleted = prune_audio(
                audio_dir=audio_dir,
                legacy_dirs=legacy,
                raw_retention_days=args.audio_raw_days,
                mixed_retention_days=args.audio_mixed_days,
                dry_run=False,
                logger=print,
            )
            print(f"  Pruned {len(deleted)} audio files.")
        print(f"  Pruned files older than {args.days} days.")
    else:
        hint = f"./echobox clean --older {args.days} --prune"
        if args.audio:
            hint += " --audio"
        print("  To delete old files, run:")
        print(f"    {hint}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
