#!/usr/bin/env python3
"""HTML report rendering helpers for Echobox publish output."""
from __future__ import annotations

import argparse
import datetime as _datetime
import html
import re
from pathlib import Path

ACTION_RE = re.compile(r"^\s*[-*]\s+\*\*\[(?P<owner>[^\]]+)\]\*\*\s+(?P<body>.+?)\s*$")
SPEAKER_RE = re.compile(
    r"^(?:\[(?P<timestamp>\d{1,2}:\d{2}(?::\d{2})?)\]\s+)?(?P<label>[A-Z][A-Z0-9_ ]+):\s*(?P<text>.+)$"
)
EMAIL_RE = re.compile(r"^\S+@\S+$")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _owner_palette(owner: str) -> tuple[str, str]:
    palettes = [
        ("rgba(255, 123, 84, 0.18)", "#ff9a7d"),
        ("rgba(115, 160, 255, 0.18)", "#95b8ff"),
        ("rgba(95, 209, 167, 0.18)", "#86e2c0"),
        ("rgba(224, 135, 255, 0.18)", "#ebb0ff"),
        ("rgba(255, 209, 102, 0.18)", "#ffe39a"),
        ("rgba(255, 140, 171, 0.18)", "#ffb4c8"),
    ]
    index = sum(ord(char) for char in owner) % len(palettes)
    return palettes[index]


def _inline_format(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _render_table(rows: list[list[str]]) -> str:
    header, body = rows[0], rows[1:]
    parts = ['<div class="table-wrap"><table><thead><tr>']
    parts.append("".join(f"<th>{_inline_format(cell)}</th>" for cell in header))
    parts.append("</tr></thead><tbody>")
    for row in body:
        normalized = row + [""] * (len(header) - len(row))
        parts.append("<tr>")
        parts.append("".join(f"<td>{_inline_format(cell)}</td>" for cell in normalized[: len(header)]))
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _close_lists(list_stack: list[int], out: list[str], target_depth: int = 0) -> None:
    while len(list_stack) > target_depth:
        out.append("</ul>")
        list_stack.pop()


def _adjust_list_depth(list_stack: list[int], out: list[str], depth: int) -> None:
    if not list_stack:
        out.append("<ul>")
        list_stack.append(depth)
        return
    while list_stack and depth < list_stack[-1]:
        out.append("</ul>")
        list_stack.pop()
    while list_stack and depth > list_stack[-1]:
        out.append("<ul>")
        list_stack.append(list_stack[-1] + 1)


def md_to_html(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    table_rows: list[list[str]] = []
    list_stack: list[int] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{_inline_format(' '.join(paragraph).strip())}</p>")
            paragraph = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            out.append(_render_table(table_rows))
            table_rows = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            code_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_table()
            _close_lists(list_stack, out)
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            _close_lists(list_stack, out)
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
                table_rows.append(cells)
            continue
        flush_table()
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            _close_lists(list_stack, out)
            level = len(heading.group(1))
            out.append(f"<h{level}>{html.escape(heading.group(2).strip())}</h{level}>")
            continue
        bullet = re.match(r"^(?P<indent>\s*)[-*]\s+(?P<body>.+)$", line)
        if bullet:
            flush_paragraph()
            depth = len(bullet.group("indent").replace("\t", "    ")) // 2
            _adjust_list_depth(list_stack, out, depth)
            body = bullet.group("body").strip()
            action = ACTION_RE.match(line)
            if action:
                owner_name = action.group("owner")
                owner = html.escape(owner_name)
                owner_slug = _slugify(owner_name)
                owner_bg, owner_fg = _owner_palette(owner_name)
                body_html = _inline_format(action.group("body"))
                out.append(
                    f'<li class="action-item"><span class="owner-tag owner-{owner_slug}" '
                    f'style="--owner-bg: {owner_bg}; --owner-fg: {owner_fg};">{owner}</span>'
                    f'<span class="action-copy">{body_html}</span></li>'
                )
            else:
                out.append(f"<li>{_inline_format(body)}</li>")
            continue
        if not stripped:
            flush_paragraph()
            _close_lists(list_stack, out)
            continue
        _close_lists(list_stack, out)
        paragraph.append(stripped)

    flush_paragraph()
    flush_table()
    _close_lists(list_stack, out)
    flush_code()
    return "\n".join(out)


SPEAKER_TABLE_RE = re.compile(
    r"\|\s*(?P<label>SPEAKER_\d+|Unknown)\s*\|\s*(?P<name>[^|]+?)\s*\|"
)


def extract_speaker_map(enrichment: str) -> dict[str, str]:
    """Parse the Speaker Identification table to map labels to real names."""
    mapping: dict[str, str] = {}
    in_table = False
    for line in enrichment.splitlines():
        stripped = line.strip()
        if "Speaker Identification" in stripped:
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                if mapping:
                    break
                continue
            # Skip separator rows
            if all(set(cell.strip()) <= {"-", ":"} for cell in stripped.strip("|").split("|")):
                continue
            m = SPEAKER_TABLE_RE.search(stripped)
            if m:
                label = m.group("label").strip()
                name = m.group("name").strip()
                if name and label != "Speaker Label" and name != "Identified As":
                    mapping[label] = name
    return mapping


def render_transcript(text: str, speaker_map: dict[str, str] | None = None) -> str:
    if not text.strip():
        return '<p class="transcript-empty">No transcript available.</p>'

    name_map = speaker_map or {}
    parts = ['<div class="transcript-lines">']
    speaker_index: dict[str, int] = {}
    next_index = 0
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        match = SPEAKER_RE.match(stripped)
        if match:
            speaker = match.group("label").strip()
            if speaker not in speaker_index:
                speaker_index[speaker] = next_index % 6
                next_index += 1
            color_class = f"speaker-{speaker_index[speaker]}"
            timestamp = match.group("timestamp")
            display_name = name_map.get(speaker, speaker)
            meta = f"{html.escape(timestamp)} &middot; {html.escape(display_name)}" if timestamp else html.escape(display_name)
            parts.append(
                f'<div class="transcript-line {color_class}"><span class="speaker-name">{meta}</span>'
                f'<span class="speaker-text">{html.escape(match.group("text"))}</span></div>'
            )
        elif stripped:
            parts.append(f'<div class="transcript-line transcript-line-plain">{html.escape(raw_line)}</div>')
        else:
            parts.append('<div class="transcript-gap"></div>')
    parts.append("</div>")
    return "".join(parts)


def _replace_speaker_section(enrichment: str, speaker_map: dict[str, str]) -> str:
    """Strip the Speaker Identification section and inject a one-line Speakers list.

    The table in the raw .md is preserved for archival and for extract_speaker_map,
    but the rendered HTML gets a compact one-liner instead.
    """
    clean_names: list[str] = []
    seen: set[str] = set()
    for name in speaker_map.values():
        stripped = name.strip()
        if not stripped or EMAIL_RE.match(stripped.split()[0]):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        clean_names.append(stripped)

    speakers_line = f"**Speakers:** {', '.join(clean_names)}." if clean_names else ""

    lines = enrichment.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        stripped = lines[i].strip()
        if not replaced and stripped == "## Speaker Identification":
            if speakers_line:
                out.append(speakers_line)
                out.append("")
            i += 1
            while i < len(lines):
                next_stripped = lines[i].strip()
                if next_stripped.startswith("## "):
                    break
                i += 1
            replaced = True
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def render_report(template: str, enrichment: str, transcript: str, title: str, today: str | None = None) -> str:
    speaker_map = extract_speaker_map(enrichment)
    enrichment_for_html = _replace_speaker_section(enrichment, speaker_map)
    today = today or _datetime.date.today().isoformat()
    rendered = template
    rendered = rendered.replace("{{TITLE}}", f"Call Report: {html.escape(title)}")
    rendered = rendered.replace("{{DATE}}", html.escape(today))
    rendered = rendered.replace("{{ENRICHMENT_CONTENT}}", md_to_html(enrichment_for_html))
    rendered = rendered.replace("{{TRANSCRIPT_CONTENT}}", render_transcript(transcript, speaker_map))
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an Echobox HTML report")
    parser.add_argument("template")
    parser.add_argument("enrichment")
    parser.add_argument("transcript")
    parser.add_argument("title")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    transcript_text = ""
    if args.transcript and args.transcript != "-" and Path(args.transcript).exists():
        transcript_text = Path(args.transcript).read_text()
    print(
        render_report(
            Path(args.template).read_text(),
            Path(args.enrichment).read_text(),
            transcript_text,
            args.title,
            today=args.date,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
