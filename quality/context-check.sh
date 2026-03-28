#!/bin/bash
# Context injection quality check — how smart is the enrichment?
# Score out of 10. Higher is better.

ECHOBOX_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ECHOBOX_DIR/pipeline/enrich.py"
SCORE=0

# 1. Calendar event matching (by transcript timestamp)
S=$(grep -c 'get_calendar_context\|calendar.*event\|transcript_date' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 2 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "1  [ok] Calendar: event lookup by date"; else echo "1  [!!] Calendar: no time-based matching"; fi

# 2. Attendee extraction from calendar event
S=$(grep -c 'attendee\|participant' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "2  [ok] Attendees: extracted from calendar"; else echo "2  [!!] Attendees: not extracted"; fi

# 3. Document context injected
S=$(grep -c 'document.*context\|documents.*command\|PROJECT_DIR' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "3  [ok] Documents: context injected"; else echo "3  [!!] Documents: not injected"; fi

# 4. Meeting type detection
S=$(grep -c 'meeting_type\|classify_call_type\|classification' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "4  [ok] Meeting type: detected"; else echo "4  [!!] Meeting type: not classified"; fi

# 5. Attendee names injected into prompt
S=$(grep -c 'attendees_block\|known_attendees\|build_attendees_block' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "5  [ok] Names: injected into prompt"; else echo "5  [!!] Names: LLM guesses speakers"; fi

# 6. Type-specific context curation
S=$(grep -c 'fetch_context_by_type\|context.*type\|meeting_types' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "6  [ok] Context: type-specific curation"; else echo "6  [!!] Context: generic dump"; fi

# 7. Message/history context
S=$(grep -c 'message_context\|messages.*enabled\|msg_query' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "7  [ok] Messages: history context available"; else echo "7  [!!] Messages: no history context"; fi

# 8. Transcript timestamp parsed
S=$(grep -c 'parse_transcript_metadata\|filename_match\|Date:' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "8  [ok] Timestamp: parsed from transcript"; else echo "8  [!!] Timestamp: not extracted"; fi

# 9. Template available for HTML fallback
S=$(ls "$ECHOBOX_DIR/templates/report.html" 2>/dev/null | wc -l | tr -d ' ')
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "9  [ok] HTML: template fallback available"; else echo "9  [!!] HTML: no template fallback"; fi

# 10. Graceful degradation without calendar
S=$(grep -c 'if.*events\|if not\|except\|fallback\|general' "$SCRIPT" 2>/dev/null)
if [ "$S" -ge 2 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "10 [ok] Fallback: graceful without calendar"; else echo "10 [!!] Fallback: may break without calendar"; fi

echo ""
echo "Context score: $SCORE/10"
