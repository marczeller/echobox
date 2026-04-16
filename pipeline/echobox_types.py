"""Shared type aliases and TypedDict shapes for the pipeline modules.

The codebase is intentionally dict-first — these names exist to give the
shapes that cross file boundaries a common vocabulary, not to enforce
runtime checks. Use them in signatures so a reader of `actions.py` or
`summary.py` does not have to chase back to `enrich.py` to learn what a
"sidecar dict" looks like.

Conventions:
- Every TypedDict here uses `total=False` because the producers (LLM
  output, markdown parsers, calendar feeds) routinely omit fields.
- `Config` is a flat dotted-key map of strings — that's literally what
  `enrich.load_config` returns after flattening the YAML.
"""
from __future__ import annotations

from typing import TypedDict


# Flattened YAML config: dotted key -> string value.
# Produced by `pipeline.enrich.load_config`, consumed everywhere.
Config = dict[str, str]


class TranscriptMeta(TypedDict, total=False):
    """Output of `parse_transcript_metadata` (enrich.py)."""

    date: str | None
    time: str | None
    duration: str | None


class Classification(TypedDict, total=False):
    """Output of `classify_call_type` (enrich.py)."""

    meeting_type: str
    matched_pattern: str | None


class Attendee(TypedDict, total=False):
    """One row of `map_attendees` output (enrich.py)."""

    email: str
    name: str


class Speaker(TypedDict, total=False):
    """One row of the `speakers` array in an enrichment sidecar."""

    label: str
    name: str
    confidence: str


class ActionItem(TypedDict, total=False):
    """One row of the `action_items` array.

    Produced by `extract_structured_data` (enrich.py) and
    `parse_markdown_actions` (actions.py); consumed in summary.py and
    actions.py.
    """

    owner: str
    task: str
    deadline: str


class EnrichmentSidecar(TypedDict, total=False):
    """JSON sidecar written next to each `*-enriched.md` file.

    See `extract_structured_data` in enrich.py for the canonical writer.
    """

    date: str | None
    time: str | None
    duration: str | None
    meeting_type: str | None
    participants: list[Attendee]
    summary: str
    speakers: list[Speaker]
    decisions: list[str]
    action_items: list[ActionItem]
    follow_ups: list[str]


__all__ = [
    "ActionItem",
    "Attendee",
    "Classification",
    "Config",
    "EnrichmentSidecar",
    "Speaker",
    "TranscriptMeta",
]
