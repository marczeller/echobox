#!/bin/bash
# Fetch calendar events for a given date.
# Returns JSON with an "items" array.
# Used by the enrichment pipeline to match transcripts to meetings.
#
# Usage: calendar.sh 2026-03-27
# Override the calendar command via ECHOBOX_CALENDAR_CMD environment variable.

export PATH="$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

DATE="$1"
if [ -z "$DATE" ]; then
    echo '{"items":[]}'
    exit 0
fi
# Validate date format to prevent shell injection via {date} substitution
if ! echo "$DATE" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
    echo "Error: invalid date format '$DATE' (expected YYYY-MM-DD)" >&2
    echo '{"items":[]}'
    exit 1
fi

CALENDAR_CMD="${ECHOBOX_CALENDAR_CMD:-}"

if [ -n "$CALENDAR_CMD" ]; then
    CMD="${CALENDAR_CMD//\{date\}/$DATE}"
    bash -c "$CMD" 2>/dev/null || echo '{"items":[]}'
else
    echo "Warning: no calendar command configured" >&2
    echo "Set ECHOBOX_CALENDAR_CMD or configure context_sources.calendar.command in echobox.yaml" >&2
    echo '{"items":[]}'
fi
