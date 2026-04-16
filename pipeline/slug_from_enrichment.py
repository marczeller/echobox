#!/usr/bin/env python3
"""Derive a meaningful slug from an Echobox enrichment JSON sidecar.

Usage: slug_from_enrichment.py <enriched.json> <fallback-slug>

Reads the JSON sidecar written by enrich.py, picks the first external
participant's email domain, and builds `{org}-meeting-{YYYY-MM-DD}`.

Prints the derived slug to stdout, or empty string on failure
(orchestrator keeps the legacy slug when stdout is empty).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from echobox_types import EnrichmentSidecar  # noqa: E402

INTERNAL_DOMAINS = {"bgdlabs.com", "aavechan.com", "aave.com"}
GENERIC_SUFFIXES = {"capital", "vc", "ventures", "xyz", "co", "io", "com", "fund", "labs"}


def _sanitize(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug


def _org_from_email(email: str) -> str | None:
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].lower()
    if domain in INTERNAL_DOMAINS:
        return None
    parts = [p for p in domain.split(".") if p and p not in GENERIC_SUFFIXES]
    if not parts:
        return None
    return parts[0]


def derive_slug(sidecar: EnrichmentSidecar, fallback: str) -> str:
    date = (sidecar.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", fallback)
        if not m:
            return ""
        date = m.group(1)

    participants = sidecar.get("participants") or []
    org: str | None = None
    for p in participants:
        email = (p.get("email") or "").strip()
        candidate = _org_from_email(email)
        if candidate:
            org = candidate
            break

    if not org:
        return ""

    slug = _sanitize(f"{org}-meeting-{date}")
    return slug


def main() -> int:
    if len(sys.argv) < 3:
        print("", end="")
        return 0
    sidecar_path = Path(sys.argv[1])
    fallback = sys.argv[2]
    if not sidecar_path.exists():
        print("", end="")
        return 0
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except Exception:
        print("", end="")
        return 0
    slug = derive_slug(sidecar, fallback)
    print(slug, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
