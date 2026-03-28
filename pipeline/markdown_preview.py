#!/usr/bin/env python3
"""Render Echobox markdown in a terminal-friendly format."""
from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path

ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"


def terminal_width(default: int = 100) -> int:
    try:
        return max(60, os.get_terminal_size().columns)
    except OSError:
        return default


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_table(rows: list[list[str]], width: int, use_ansi: bool) -> list[str]:
    if not rows:
        return []

    columns = max(len(row) for row in rows)
    normalized = [row + [""] * (columns - len(row)) for row in rows]
    max_total = width - (columns - 1) * 3
    max_total = max(max_total, columns * 12)

    col_widths = []
    for index in range(columns):
        longest = max(len(row[index]) for row in normalized)
        col_widths.append(min(max(longest, 10), max_total // columns))

    rendered = []
    for row_index, row in enumerate(normalized):
        wrapped = [
            textwrap.wrap(cell or "", width=col_widths[idx]) or [""]
            for idx, cell in enumerate(row)
        ]
        height = max(len(cell_lines) for cell_lines in wrapped)
        for line_index in range(height):
            parts = []
            for col_index, cell_lines in enumerate(wrapped):
                text = cell_lines[line_index] if line_index < len(cell_lines) else ""
                if row_index == 0 and use_ansi:
                    text = f"{ANSI_BOLD}{text}{ANSI_RESET}"
                parts.append(text.ljust(col_widths[col_index]))
            rendered.append(" | ".join(parts).rstrip())
        if row_index == 0:
            rendered.append("-" * min(width, len(_strip_ansi(rendered[-1]))))
    return rendered


def render_markdown(text: str, width: int | None = None, use_ansi: bool = True) -> str:
    width = width or terminal_width()
    output: list[str] = []
    table_rows: list[list[str]] = []
    in_code = False

    def flush_table():
        nonlocal table_rows
        if table_rows:
            output.extend(_format_table(table_rows, width, use_ansi))
            output.append("")
            table_rows = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_table()
            in_code = not in_code
            continue

        if in_code:
            prefix = "> "
            content = f"{prefix}{line}" if line else prefix.rstrip()
            if use_ansi:
                content = f"{ANSI_DIM}{content}{ANSI_RESET}"
            output.append(content)
            continue

        if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
            maybe_row = [cell.strip() for cell in stripped.strip("|").split("|")]
            if maybe_row and not all(set(cell) <= {"-", ":"} for cell in maybe_row):
                table_rows.append(maybe_row)
                continue
            if table_rows:
                continue

        flush_table()

        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            if level <= 2:
                heading = heading.upper()
            if use_ansi:
                heading = f"{ANSI_CYAN}{ANSI_BOLD}{heading}{ANSI_RESET}"
            output.append(heading)
            output.append("")
            continue

        bullet_match = re.match(r"^([-*])\s+(.*)$", stripped)
        if bullet_match:
            wrapped = textwrap.wrap(
                bullet_match.group(2),
                width=max(20, width - 4),
                subsequent_indent="    ",
            ) or [bullet_match.group(2)]
            output.append(f"- {wrapped[0]}")
            output.extend(f"  {line}" for line in wrapped[1:])
            continue

        wrapped = textwrap.wrap(stripped, width=width) or [stripped]
        output.extend(wrapped)

    flush_table()
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="Preview markdown in the terminal")
    parser.add_argument("file", help="Markdown file to render")
    parser.add_argument("--plain", action="store_true", help="Disable ANSI styling")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    print(render_markdown(path.read_text(), use_ansi=not args.plain))


if __name__ == "__main__":
    main()
