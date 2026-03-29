#!/bin/bash
# Publish call report as HTML (optionally password-gated on Vercel)
# Uses Claude CLI (OAuth) to generate designed HTML, or falls back to template.

set -e

ECHOBOX_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$HOME/echobox-data"
STATE_DIR="$DATA_DIR"
REPORT_DIR="$DATA_DIR/reports"
TRANSCRIPT_DIR="$DATA_DIR/transcripts"
TEMPLATE="$ECHOBOX_DIR/templates/report.html"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:$PATH"
ECHOBOX_PYTHON="${ECHOBOX_PYTHON:-python3}"

CONFIG="$ECHOBOX_DIR/config/echobox.yaml"

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
        eval "$key=$value"
    done <<EOF
$paths_output
EOF
    DATA_DIR="${DATA_DIR:-$HOME/echobox-data}"
    TRANSCRIPT_DIR="${TRANSCRIPT_DIR:-$DATA_DIR/transcripts}"
    REPORT_DIR="${REPORT_DIR:-$DATA_DIR/reports}"
    STATE_DIR="${STATE_DIR:-$(dirname "$REPORT_DIR")}"
    mkdir -p "$REPORT_DIR" "$STATE_DIR"
}

resolve_paths

ENRICHMENT="$1"
if [ -z "$ENRICHMENT" ] || [ ! -f "$ENRICHMENT" ]; then
    echo "Usage: publish.sh <enrichment.md>"
    exit 1
fi

MEETING_NAME=$(basename "$ENRICHMENT" .md)
SLUG=$(echo "$MEETING_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g')
SITE_DIR="$REPORT_DIR/$SLUG"
TRANSCRIPT_ID=$(echo "$MEETING_NAME" | sed -E 's/-(enriched|raw)$//')
RAW_FILE="$TRANSCRIPT_DIR/${TRANSCRIPT_ID}.txt"
if [ ! -f "$RAW_FILE" ]; then
    ALT_RAW_FILE="$(dirname "$ENRICHMENT")/${TRANSCRIPT_ID}.txt"
    if [ -f "$ALT_RAW_FILE" ]; then
        RAW_FILE="$ALT_RAW_FILE"
    else
        echo "Error: matching transcript not found for $ENRICHMENT"
        echo "Expected: $RAW_FILE"
        echo "  Or place ${TRANSCRIPT_ID}.txt next to the enrichment file before publishing."
        exit 1
    fi
fi

mkdir -p "$SITE_DIR/api"

PUBLISH_ENGINE="${ECHOBOX_PUBLISH_ENGINE:-$(read_config 'publish.engine' 'local')}"

GENERATED=false

if [ "$PUBLISH_ENGINE" = "claude" ] && command -v claude &>/dev/null; then
    echo "Generating HTML with Claude CLI (content sent to Anthropic API)..."
    PROMPT_FILE=$(mktemp)
    cat > "$PROMPT_FILE" << 'PROMPT_HEADER'
Generate a single-file HTML call report. Output ONLY valid HTML from <!DOCTYPE html> to </html>.
Design system: bg #08080c, cards #111118, text #ece8e0, dim #6a665e, accent #c62828.
Fonts: Bricolage Grotesque (Google Fonts) for body, Fragment Mono for labels.
Sticky frosted-glass header with title + Report/Transcript tab switcher.
Max-width 780px. Stat cards grid at top. h2 with border-top. h3 in Fragment Mono red uppercase.
Tables with mono headers. .pill badges. .position callouts with left red border.
Two tabs: Report (designed analysis, default) + Transcript (raw mono text).
PROMPT_HEADER
    echo "" >> "$PROMPT_FILE"
    echo "Meeting enrichment for Report tab:" >> "$PROMPT_FILE"
    cat "$ENRICHMENT" >> "$PROMPT_FILE"
    echo "" >> "$PROMPT_FILE"
    echo "Raw transcript for Transcript tab:" >> "$PROMPT_FILE"
    head -200 "$RAW_FILE" >> "$PROMPT_FILE"

    CLAUDE_MODEL="${ECHOBOX_CLAUDE_MODEL:-claude-sonnet-4-6}"
    ANTHROPIC_API_KEY="" claude -p --model "$CLAUDE_MODEL" < "$PROMPT_FILE" | sed '/^```/d' > "$SITE_DIR/report.html" 2>/dev/null

    if [ -s "$SITE_DIR/report.html" ] && [ "$(wc -c < "$SITE_DIR/report.html")" -ge 500 ]; then
        GENERATED=true
    else
        echo "  Claude generation failed, falling back to local template"
    fi
    rm -f "$PROMPT_FILE"
fi

if [ "$GENERATED" = false ]; then
    echo "Generating HTML report (local, no network)..."
    if [ -f "$TEMPLATE" ]; then
        $ECHOBOX_PYTHON "$ECHOBOX_DIR/pipeline/report_render.py" \
            "$TEMPLATE" "$ENRICHMENT" "$RAW_FILE" "$MEETING_NAME" \
            > "$SITE_DIR/report.html"
        GENERATED=true
    else
        echo "Error: template not found: $TEMPLATE"
        exit 1
    fi
fi

PUBLISH_PLATFORM="${ECHOBOX_PUBLISH_PLATFORM:-$(read_config 'publish.platform' 'local')}"
PUBLISH_PASSWORD="${ECHOBOX_PUBLISH_PASSWORD:-$(read_config 'publish.password' '')}"
PUBLISH_SCOPE="${ECHOBOX_PUBLISH_SCOPE:-$(read_config 'publish.scope' '')}"

if [ "$PUBLISH_PLATFORM" = "vercel" ] && command -v vercel &>/dev/null; then
    if [ -z "$PUBLISH_PASSWORD" ]; then
        echo "ERROR: Set ECHOBOX_PUBLISH_PASSWORD before deploying to Vercel."
        echo "  Reports deployed without a password are publicly readable."
        echo "  export ECHOBOX_PUBLISH_PASSWORD='your-secure-password'"
        echo ""
        echo "Falling back to local publish."
        PUBLISH_PLATFORM="local"
    fi
fi

if [ "$PUBLISH_PLATFORM" = "vercel" ] && command -v vercel &>/dev/null; then
    GATE_JS="$ECHOBOX_DIR/templates/gate.js"
    if [ -f "$GATE_JS" ]; then
        $ECHOBOX_PYTHON -c "
import sys
content = open(sys.argv[1]).read()
password = sys.argv[2].replace('\\', '\\\\').replace(\"'\", \"\\'\")
print(content.replace('ECHOBOX_DEFAULT_PASSWORD', password))
" "$GATE_JS" "$PUBLISH_PASSWORD" > "$SITE_DIR/api/gate.js"
    fi

    cat > "$SITE_DIR/vercel.json" << 'VJ'
{"rewrites": [{"source": "/(.*)", "destination": "/api/gate"}]}
VJ

    echo ".vercel/" > "$SITE_DIR/.gitignore"

    SCOPE_FLAG=""
    if [ -n "$PUBLISH_SCOPE" ]; then
        SCOPE_FLAG="--scope $PUBLISH_SCOPE"
    fi

    DEPLOY_URL=$(cd "$SITE_DIR" && vercel --yes $SCOPE_FLAG 2>&1 | grep -oE "https://[a-z0-9-]+\.vercel\.app" | tail -1)

    if [ -z "$DEPLOY_URL" ]; then
        echo "Vercel deploy failed, report saved locally: $SITE_DIR/report.html"
    else
        echo "$DEPLOY_URL" > "$STATE_DIR/last-report-url"
        echo "Published: $DEPLOY_URL"
    fi
else
    echo "Report saved: $SITE_DIR/report.html"
    echo "file://$SITE_DIR/report.html" > "$STATE_DIR/last-report-url"
    echo "  Open: ./echobox open"
fi
