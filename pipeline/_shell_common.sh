# Shared shell helpers for Echobox pipeline scripts.
#
# Usage:
#   ECHOBOX_DIR="$(cd "$(dirname "$0")/.." && pwd)"
#   CONFIG="$ECHOBOX_DIR/config/echobox.yaml"
#   ECHOBOX_PYTHON="${ECHOBOX_PYTHON:-python3}"
#   # shellcheck disable=SC1091
#   . "$ECHOBOX_DIR/pipeline/_shell_common.sh"
#
# The sourcing script owns ECHOBOX_DIR, CONFIG, and ECHOBOX_PYTHON; this file
# only provides functions that reference them. Do not set those vars here.

# Read a single dotted key from the Echobox config, honoring the same defaults
# as pipeline/read_config.py. Empty results fall back to the caller's default.
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

# Resolve Echobox data directories from config + env, export them as globals,
# and mkdir each. Sets DATA_DIR, TRANSCRIPT_DIR, AUDIO_DIR, ENRICHMENT_DIR,
# REPORT_DIR, LOG_DIR, STATE_DIR. Callers can ignore dirs they don't need.
resolve_paths() {
    local paths_output key value
    paths_output=$("$ECHOBOX_PYTHON" "$ECHOBOX_DIR/pipeline/read_config.py" paths "$CONFIG" 2>/dev/null || true)
    while IFS='=' read -r key value; do
        [ -n "$key" ] || continue
        case "$key" in
            DATA_DIR|TRANSCRIPT_DIR|AUDIO_DIR|ENRICHMENT_DIR|REPORT_DIR|LOG_DIR|STATE_DIR)
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
