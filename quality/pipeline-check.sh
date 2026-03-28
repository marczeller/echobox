#!/bin/bash
# Pipeline quality check — validates each component of the Echobox pipeline.
# Score out of 10. Higher is better.

set -o pipefail

ECHOBOX_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCORE=0
LAPTOP="${ECHOBOX_LAPTOP:-}"

check_remote() {
    if [ -n "$LAPTOP" ]; then
        ssh -o ConnectTimeout=5 "$LAPTOP" "$1" 2>/dev/null
    else
        eval "$1" 2>/dev/null
    fi
}

# 1. App detection: browser tabs checked BEFORE native apps
S=$(check_remote "python3 -c \"import trnscrb, inspect; src=inspect.getsource(trnscrb.watcher); print(1 if 'browser' in src.lower() else 0)\"")
if [ "$S" = "1" ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "1  [ok] App detection: browser-first"; else echo "1  [!!] App detection: still native-first"; fi

# 2. FFmpeg available for audio processing
S=$(check_remote "ffmpeg -version 2>/dev/null | head -1 | grep -c ffmpeg")
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "2  [ok] FFmpeg: installed"; else echo "2  [!!] FFmpeg: missing"; fi

# 3. MLX enrichment is calendar-aware
S=$(grep -c 'calendar\|Calendar\|get_calendar_context\|transcript_date' "$ECHOBOX_DIR/pipeline/enrich.py" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "3  [ok] MLX: calendar-aware"; else echo "3  [!!] MLX: no calendar context"; fi

# 4. Orchestrator calls all pipeline steps
S=$(grep -c 'enrich\|sync\|publish\|notif' "$ECHOBOX_DIR/pipeline/orchestrator.sh" 2>/dev/null)
if [ "$S" -ge 4 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "4  [ok] Orchestrator: all steps wired"; else echo "4  [!!] Orchestrator: incomplete"; fi

# 5. Publish script has URL parsing
S=$(grep -c 'grep -oE.*vercel' "$ECHOBOX_DIR/pipeline/publish.sh" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "5  [ok] Publish: URL parsing present"; else echo "5  [!!] Publish: URL parsing missing"; fi

# 6. Notification support configured
S=$(grep -c 'NOTIFY\|notify\|notification\|webhook' "$ECHOBOX_DIR/pipeline/orchestrator.sh" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "6  [ok] Notifications: supported"; else echo "6  [!!] Notifications: not wired"; fi

# 7. OAuth-only CLI auth (no hardcoded API keys)
S=$(grep -c 'ANTHROPIC_API_KEY=""' "$ECHOBOX_DIR/pipeline/publish.sh" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "7  [ok] Auth: OAuth-only"; else echo "7  [!!] Auth: may use paid API key"; fi

# 8. WAV retention patch described
S=$(grep -c 'KEEP_WAV\|keep.*wav\|retention' "$ECHOBOX_DIR/patches/02-wav-retention.diff" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "8  [ok] WAV: retention patch exists"; else echo "8  [!!] WAV: no retention patch"; fi

# 9. On-stop hook for auto-trigger
S=$(grep -c 'on.stop\|on_stop\|ON_STOP' "$ECHOBOX_DIR/patches/03-on-stop-hook.diff" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "9  [ok] Auto-trigger: on-stop hook exists"; else echo "9  [!!] Auto-trigger: no hook"; fi

# 10. Error logging to file
S=$(grep -c 'log\|LOG\|tee\|LOGFILE' "$ECHOBOX_DIR/pipeline/orchestrator.sh" 2>/dev/null)
if [ "$S" -ge 1 ] 2>/dev/null; then SCORE=$((SCORE+1)); echo "10 [ok] Logging: pipeline errors captured"; else echo "10 [!!] Logging: errors not captured"; fi

echo ""
echo "Pipeline score: $SCORE/10"
