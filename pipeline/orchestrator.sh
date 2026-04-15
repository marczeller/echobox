#!/bin/bash
# Post-call pipeline orchestrator
# Triggered by echobox watch or manually: orchestrator.sh <transcript_id>

set -eo pipefail

ECHOBOX_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$ECHOBOX_DIR/config/echobox.yaml"
DATA_DIR="$HOME/echobox-data"
STATE_DIR="$DATA_DIR"
LOG_DIR="$DATA_DIR/logs"
ENRICHMENT_DIR="$DATA_DIR/enrichments"
TRANSCRIPT_DIR="$DATA_DIR/transcripts"
REPORT_DIR="$DATA_DIR/reports"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:$PATH"
ECHOBOX_PYTHON="${ECHOBOX_PYTHON:-python3}"

read_config() {
    local key="$1" default="$2"
    local val
    val=$(
        "$ECHOBOX_PYTHON" - "$CONFIG" "$key" "$default" <<'PY' 2>/dev/null
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).parent.parent))
from pipeline.read_config import read_value

print(read_value(Path(sys.argv[1]), sys.argv[2], sys.argv[3]), end="")
PY
    )
    echo "${val:-$default}"
}

resolve_paths() {
    local paths_output key value
    paths_output=$("$ECHOBOX_PYTHON" "$ECHOBOX_DIR/pipeline/read_config.py" paths "$CONFIG" 2>/dev/null || true)
    while IFS='=' read -r key value; do
        [ -n "$key" ] || continue
        case "$key" in
            DATA_DIR|TRANSCRIPT_DIR|AUDIO_DIR|ENRICHMENT_DIR|REPORT_DIR|LOG_DIR)
                printf -v "$key" '%s' "$value" ;;
        esac
    done <<EOF
$paths_output
EOF
    DATA_DIR="${DATA_DIR:-$HOME/echobox-data}"
    TRANSCRIPT_DIR="${TRANSCRIPT_DIR:-$DATA_DIR/transcripts}"
    AUDIO_DIR="${AUDIO_DIR:-$DATA_DIR/audio}"
    ENRICHMENT_DIR="${ENRICHMENT_DIR:-$DATA_DIR/enrichments}"
    REPORT_DIR="${REPORT_DIR:-$DATA_DIR/reports}"
    LOG_DIR="${LOG_DIR:-$DATA_DIR/logs}"
    STATE_DIR="${STATE_DIR:-$(dirname "$REPORT_DIR")}"
    mkdir -p "$LOG_DIR" "$TRANSCRIPT_DIR" "$AUDIO_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR" "$STATE_DIR"
}

resolve_paths

LOGFILE="$LOG_DIR/pipeline.log"
RESULTS_LOG="$LOG_DIR/pipeline-results.jsonl"
if [ "${ECHOBOX_DISABLE_TEE_LOGGING:-false}" = "true" ]; then
    exec >>"$LOGFILE" 2>&1
else
    exec > >(tee -a "$LOGFILE") 2>&1
fi
echo "======== Pipeline run: $(date -Iseconds) ========"

write_pipeline_result() {
    local transcript_id="$1"
    local transcript_file="$2"
    local enrichment_file="$3"
    local report_file="$4"
    local enrichment_status="$5"

    $ECHOBOX_PYTHON - "$RESULTS_LOG" "$transcript_id" "$transcript_file" "$enrichment_file" "$report_file" "$enrichment_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

log_path = Path(sys.argv[1])
transcript_id = sys.argv[2]
transcript_file = Path(sys.argv[3])
enrichment_file = Path(sys.argv[4])
report_file = Path(sys.argv[5])
enrichment_status = sys.argv[6]
sidecar_path = enrichment_file.with_suffix(".json")

metrics = {}
if sidecar_path.exists():
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except json.JSONDecodeError:
        sidecar = {}
    metrics = {
        "speaker_count": len(sidecar.get("speakers", [])),
        "action_item_count": len(sidecar.get("action_items", [])),
        "decision_count": len(sidecar.get("decisions", [])),
        "participant_count": len(sidecar.get("participants", [])),
        "follow_up_count": len(sidecar.get("follow_ups", [])),
        "meeting_type": sidecar.get("meeting_type", ""),
    }

payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "transcript_id": transcript_id,
    "transcript_path": str(transcript_file),
    "enrichment_path": str(enrichment_file),
    "report_path": str(report_file) if report_file.exists() else "",
    "enrichment_status": enrichment_status,
    "sidecar_path": str(sidecar_path) if sidecar_path.exists() else "",
    "metrics": metrics,
}

log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a") as fh:
    fh.write(json.dumps(payload) + "\n")
print(json.dumps(payload))
PY
}

TRANSCRIPT_ID="$1"
if [ -z "$TRANSCRIPT_ID" ]; then
    echo "Usage: orchestrator.sh <transcript_id>"
    exit 1
fi

TRANSCRIPT_FILE="$TRANSCRIPT_DIR/${TRANSCRIPT_ID}.txt"
if [ ! -f "$TRANSCRIPT_FILE" ]; then
    echo "Error: $TRANSCRIPT_FILE not found"
    exit 1
fi

WORKSTATION="${ECHOBOX_WORKSTATION:-$(read_config workstation_ssh '')}"
MEETING_NOTES_SSH="$(read_config 'meeting_notes.ssh_host' '')"
MEETING_NOTES_DIR="$(read_config 'meeting_notes.base_dir' '~/meeting-notes')"
ENRICHED_ENRICHMENT="$ENRICHMENT_DIR/${TRANSCRIPT_ID}-enriched.md"
RAW_ENRICHMENT="$ENRICHMENT_DIR/${TRANSCRIPT_ID}-raw.md"
ENRICHMENT="$ENRICHED_ENRICHMENT"

MLX_URL="${ECHOBOX_MLX_URL:-$(read_config mlx_url 'http://localhost:8090/v1/chat/completions')}"
MLX_MODELS_URL="${MLX_URL%/chat/completions}/models"

ENRICHMENT_STATUS="enriched"

use_raw_transcript() {
    local reason="$1"
    echo "      $reason"
    echo "      To retry: ./echobox enrich $TRANSCRIPT_FILE"
    [ -f "$ENRICHED_ENRICHMENT" ] && mv "$ENRICHED_ENRICHMENT" "${ENRICHED_ENRICHMENT}.bak"
    [ -f "${ENRICHED_ENRICHMENT%.md}.json" ] && mv "${ENRICHED_ENRICHMENT%.md}.json" "${ENRICHED_ENRICHMENT%.md}.json.bak"
    cp "$TRANSCRIPT_FILE" "$RAW_ENRICHMENT"
    ENRICHMENT="$RAW_ENRICHMENT"
    ENRICHMENT_STATUS="raw"
}

echo "[1/5] LLM enrichment with project context..."
if [ -n "$WORKSTATION" ]; then
    REMOTE_TRANSCRIPT="$(basename "$TRANSCRIPT_FILE" | sed 's/[^a-zA-Z0-9._-]/_/g')"
    REMOTE_ENRICHMENT="$(basename "$ENRICHED_ENRICHMENT" | sed 's/[^a-zA-Z0-9._-]/_/g')"
    REMOTE_SIDECAR="${REMOTE_ENRICHMENT%.md}.json"
    echo "      Syncing transcript to workstation..."
    if ! rsync -az "$TRANSCRIPT_FILE" "$WORKSTATION:~/echobox-data/transcripts/$REMOTE_TRANSCRIPT"; then
        use_raw_transcript "rsync to workstation failed — saving raw transcript as $(basename "$RAW_ENRICHMENT")"
    elif ssh -o ConnectTimeout=10 "$WORKSTATION" \
        "cd ~/echobox && python3 pipeline/enrich.py ~/echobox-data/transcripts/$REMOTE_TRANSCRIPT -o ~/echobox-data/enrichments/$REMOTE_ENRICHMENT"; then
        rsync -az "$WORKSTATION:~/echobox-data/enrichments/$REMOTE_ENRICHMENT" "$ENRICHMENT"
        rsync -az "$WORKSTATION:~/echobox-data/enrichments/$REMOTE_SIDECAR" "${ENRICHMENT%.md}.json" 2>/dev/null || true
        rm -f "$RAW_ENRICHMENT"
        echo "      Done: $ENRICHMENT"
    else
        use_raw_transcript "Workstation enrichment failed — saving raw transcript as $(basename "$RAW_ENRICHMENT")"
    fi
elif curl -sf "$MLX_MODELS_URL" >/dev/null 2>&1; then
    $ECHOBOX_PYTHON "$ECHOBOX_DIR/pipeline/enrich.py" "$TRANSCRIPT_FILE" -o "$ENRICHMENT" || {
        use_raw_transcript "LLM enrichment failed — saving raw transcript as $(basename "$RAW_ENRICHMENT")"
    }
    if [ "$ENRICHMENT_STATUS" = "enriched" ]; then
        rm -f "$RAW_ENRICHMENT"
    fi
    echo "      Done: $ENRICHMENT"
else
    echo "      LLM server not running at $MLX_URL"
    use_raw_transcript "Saving raw transcript as $(basename "$RAW_ENRICHMENT")"
fi

if [ "$ENRICHMENT_STATUS" = "enriched" ] && [ -f "${ENRICHMENT%.md}.json" ]; then
    echo "[1.5/5] Deriving meaningful slug from enrichment..."
    SIDECAR_FOR_SLUG="${ENRICHMENT%.md}.json"
    NEW_SLUG=$("$ECHOBOX_PYTHON" "$ECHOBOX_DIR/pipeline/slug_from_enrichment.py" \
        "$SIDECAR_FOR_SLUG" "$TRANSCRIPT_ID" 2>/dev/null || echo "")
    if [ -n "$NEW_SLUG" ] && [ "$NEW_SLUG" != "$TRANSCRIPT_ID" ]; then
        NEW_TRANSCRIPT_FILE="$TRANSCRIPT_DIR/${NEW_SLUG}.txt"
        NEW_ENRICHMENT="$ENRICHMENT_DIR/${NEW_SLUG}-enriched.md"
        NEW_SIDECAR="$ENRICHMENT_DIR/${NEW_SLUG}-enriched.json"
        if [ -e "$NEW_TRANSCRIPT_FILE" ] || [ -e "$NEW_ENRICHMENT" ]; then
            NEW_SLUG="${NEW_SLUG}-${TRANSCRIPT_ID##*_}"
            NEW_TRANSCRIPT_FILE="$TRANSCRIPT_DIR/${NEW_SLUG}.txt"
            NEW_ENRICHMENT="$ENRICHMENT_DIR/${NEW_SLUG}-enriched.md"
            NEW_SIDECAR="$ENRICHMENT_DIR/${NEW_SLUG}-enriched.json"
        fi
        mv "$TRANSCRIPT_FILE" "$NEW_TRANSCRIPT_FILE"
        mv "$ENRICHMENT" "$NEW_ENRICHMENT"
        mv "$SIDECAR_FOR_SLUG" "$NEW_SIDECAR"
        TRANSCRIPT_ID="$NEW_SLUG"
        TRANSCRIPT_FILE="$NEW_TRANSCRIPT_FILE"
        ENRICHMENT="$NEW_ENRICHMENT"
        ENRICHED_ENRICHMENT="$NEW_ENRICHMENT"
        echo "      Renamed to: ${NEW_SLUG}"
    else
        echo "      Keeping original slug: ${TRANSCRIPT_ID}"
    fi
fi

if [ -n "$WORKSTATION" ]; then
    echo "[2/5] Syncing to workstation..."
    for attempt in 1 2 3; do
        rsync -az "$TRANSCRIPT_DIR/" "$WORKSTATION:~/echobox-data/transcripts/" && \
        rsync -az "$ENRICHMENT_DIR/" "$WORKSTATION:~/echobox-data/enrichments/" && \
        echo "      Synced" && break
        echo "      Retry $attempt/3..." && sleep 5
    done
else
    echo "[2/5] Single-machine mode, skipping sync"
fi

echo "[3/5] Publishing call report..."
bash "$ECHOBOX_DIR/pipeline/publish.sh" "$ENRICHMENT" 2>&1 || echo "      Publish skipped"

echo "[4/5] Syncing to meeting notes..."
if [ -n "$MEETING_NOTES_SSH" ] && [ "$ENRICHMENT_STATUS" = "enriched" ]; then
    NOTES_FILENAME="$(echo "$TRANSCRIPT_ID" | tr '_' '-').md"
    NOTES_JSON_FILENAME="$(echo "$TRANSCRIPT_ID" | tr '_' '-').json"
    RAW_FILENAME="$(echo "$TRANSCRIPT_ID" | tr '_' '-').txt"
    SIDECAR="${ENRICHMENT%.md}.json"

    ssh -o ConnectTimeout=10 "$MEETING_NOTES_SSH" \
        "mkdir -p ${MEETING_NOTES_DIR}/{enrichments,raw-transcripts}" 2>/dev/null || true

    scp -o ConnectTimeout=10 "$ENRICHMENT" \
        "${MEETING_NOTES_SSH}:${MEETING_NOTES_DIR}/${NOTES_FILENAME}" && \
        echo "      Copied enrichment: $NOTES_FILENAME" || \
        echo "      Failed to copy enrichment"

    if [ -f "$SIDECAR" ]; then
        scp -o ConnectTimeout=10 "$SIDECAR" \
            "${MEETING_NOTES_SSH}:${MEETING_NOTES_DIR}/enrichments/${NOTES_JSON_FILENAME}" && \
            echo "      Copied sidecar: enrichments/$NOTES_JSON_FILENAME" || \
            echo "      Failed to copy sidecar"
    fi

    if [ -f "$TRANSCRIPT_FILE" ]; then
        scp -o ConnectTimeout=10 "$TRANSCRIPT_FILE" \
            "${MEETING_NOTES_SSH}:${MEETING_NOTES_DIR}/raw-transcripts/${RAW_FILENAME}" && \
            echo "      Copied transcript: raw-transcripts/$RAW_FILENAME" || \
            echo "      Failed to copy transcript"
    fi

    REPORT_SLUG_SYNC=$(echo "$(basename "$ENRICHMENT" .md)" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g')
    LOCAL_REPORT="$REPORT_DIR/$REPORT_SLUG_SYNC/report.html"
    REMOTE_REPORTS_DIR="$(read_config 'meeting_notes.reports_dir' '~/echobox-reports/reports')"
    if [ -f "$LOCAL_REPORT" ]; then
        ssh -o ConnectTimeout=10 "$MEETING_NOTES_SSH" \
            "mkdir -p ${REMOTE_REPORTS_DIR}/${REPORT_SLUG_SYNC}" 2>/dev/null || true
        scp -o ConnectTimeout=10 "$LOCAL_REPORT" \
            "${MEETING_NOTES_SSH}:${REMOTE_REPORTS_DIR}/${REPORT_SLUG_SYNC}/report.html" && \
            echo "      Copied report: $REPORT_SLUG_SYNC/report.html" || \
            echo "      Failed to copy report"
    fi
elif [ -z "$MEETING_NOTES_SSH" ]; then
    echo "      Meeting notes sync not configured"
else
    echo "      Skipping meeting notes sync (enrichment status: $ENRICHMENT_STATUS)"
fi

echo "[5/5] Sending notification..."
NOTIFY_ENABLED="${ECHOBOX_NOTIFY_ENABLED:-$(read_config 'notify.enabled' 'false')}"
NOTIFY_CMD="${ECHOBOX_NOTIFY_CMD:-$(read_config 'notify.command' '')}"
if [ "$NOTIFY_ENABLED" != "true" ]; then
    echo "      Notifications disabled"
elif [ -n "$NOTIFY_CMD" ]; then
    REPORT_URL=$(cat "$STATE_DIR/last-report-url" 2>/dev/null || echo "")
    export ECHOBOX_REPORT_URL="$REPORT_URL"

    MEETING_SUMMARY=""
    if [ -f "$ENRICHMENT" ] && [ "$ENRICHMENT_STATUS" = "enriched" ]; then
        MEETING_SUMMARY=$(grep -A 3 "^## Meeting Summary" "$ENRICHMENT" 2>/dev/null | tail -n +2 | head -2 | tr '\n' ' ' | cut -c1-200)
    fi
    export ECHOBOX_REPORT_TITLE="${MEETING_SUMMARY:-Call report: $TRANSCRIPT_ID}"
    export ECHOBOX_REPORT_SUMMARY="$MEETING_SUMMARY"

    NOTIFY_LOG="$LOG_DIR/notifications.log"
    # The audit log captures the full stdout+stderr of the notify command,
    # which can include passwords embedded in the command string (e.g. the
    # default config pins a shared report password in the title). Keep it
    # user-only readable so other local accounts can't skim secrets out.
    if [ ! -e "$NOTIFY_LOG" ]; then
        : > "$NOTIFY_LOG"
    fi
    chmod 600 "$NOTIFY_LOG" 2>/dev/null || true
    chmod 700 "$LOG_DIR" 2>/dev/null || true
    NOTIFY_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    {
        printf '=== %s  %s ===\n' "$NOTIFY_TS" "$TRANSCRIPT_ID"
        printf 'title: %s\n' "$ECHOBOX_REPORT_TITLE"
        printf 'url:   %s\n' "$ECHOBOX_REPORT_URL"
    } >> "$NOTIFY_LOG"
    NOTIFY_OUTPUT=$(bash -c "$NOTIFY_CMD" 2>&1)
    NOTIFY_EXIT=$?
    printf 'exit=%s\noutput:\n%s\n\n' "$NOTIFY_EXIT" "$NOTIFY_OUTPUT" >> "$NOTIFY_LOG"
    if [ "$NOTIFY_EXIT" -ne 0 ]; then
        echo "      Notification failed (exit=$NOTIFY_EXIT, see $NOTIFY_LOG)"
    fi
else
    echo "      Notifications not configured"
fi

echo ""
echo "Pipeline complete for $TRANSCRIPT_ID"
echo "  Enrichment: $ENRICHMENT_STATUS"
echo "  View: ./echobox list"
if [ "$ENRICHMENT_STATUS" = "raw" ]; then
    echo "  Re-enrich: ./echobox enrich $TRANSCRIPT_FILE"
fi
echo "  Open report: ./echobox open"

REPORT_BASENAME="$(basename "$ENRICHMENT" .md)"
REPORT_SLUG=$(echo "$REPORT_BASENAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g')
REPORT_FILE="$REPORT_DIR/$REPORT_SLUG/report.html"
write_pipeline_result "$TRANSCRIPT_ID" "$TRANSCRIPT_FILE" "$ENRICHMENT" "$REPORT_FILE" "$ENRICHMENT_STATUS"

# Wait for async tee process to flush all output to the log file
wait
