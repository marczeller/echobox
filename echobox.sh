#!/bin/bash
set -e

ECHOBOX_DIR="$(cd "$(dirname "$0")" && pwd)"
ECHOBOX_VERSION=$(cat "$ECHOBOX_DIR/VERSION" 2>/dev/null || echo "dev")
CONFIG="$ECHOBOX_DIR/config/echobox.yaml"
DATA_DIR="$HOME/echobox-data"
STATE_DIR="$DATA_DIR"
LOG_DIR="$DATA_DIR/logs"
TRANSCRIPT_DIR="$DATA_DIR/transcripts"
ENRICHMENT_DIR="$DATA_DIR/enrichments"
REPORT_DIR="$DATA_DIR/reports"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:$HOME/.local/bin:$PATH"

# Find Python 3.12+ (install.sh validates this)
PYTHON_CMD=""
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ] 2>/dev/null; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done
PYTHON_CMD="${PYTHON_CMD:-python3}"
export ECHOBOX_PYTHON="$PYTHON_CMD"

usage() {
    cat << EOF
Echobox $ECHOBOX_VERSION — Self-hosted call intelligence pipeline

Getting started:
  echobox status              Check what's installed and configured
  echobox setup               Interactive config wizard
  echobox fit                 Pick the best models for your hardware
  echobox demo                Try the pipeline on sample data (no server needed)

Daily use:
  echobox watch               Auto-record and process calls (macOS)
  echobox list                Show recent calls and their status
  echobox open [report]       Open a report in your browser
  echobox search <term>       Search across all calls
  echobox preview [call]      Preview enrichment in terminal
  echobox actions             Action items across all calls
  echobox summary [N|--month]  Summary of calls, decisions, actions (default: 7 days)

Pipeline:
  echobox enrich <file>       Run LLM enrichment on a transcript
  echobox publish <file>      Generate HTML report from enrichment
  echobox reprocess <name>    Re-enrich and re-publish a call

More:
  echobox clean [--older N] [--prune]  Show disk usage and optionally prune old data
  echobox config              Show parsed config values
  echobox quality             Run quality checks
  echobox test                Run smoke tests
  echobox version             Print version
EOF
}

cmd_version() {
    echo "echobox $ECHOBOX_VERSION"
}

die() {
    echo "Error: $1" >&2
    exit 1
}

require_file() {
    local path="$1"
    local message="$2"
    [ -f "$path" ] || die "$message"
}

latest_enrichment() {
    find "$ENRICHMENT_DIR" -maxdepth 1 -name "*.md" 2>/dev/null | sort -r | head -1
}
report_slug_for_name() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g'
}

json_sidecar_for_enrichment() {
    local enrichment="$1"
    local sidecar="${enrichment%.md}.json"
    if [ -f "$sidecar" ]; then
        echo "$sidecar"
    fi
}

resolve_enrichment_input() {
    local target="$1"

    if [ -z "$target" ]; then
        latest_enrichment
        return
    fi

    if [ -f "$target" ]; then
        echo "$target"
        return
    fi

    if [ -f "$ENRICHMENT_DIR/$target" ]; then
        echo "$ENRICHMENT_DIR/$target"
        return
    fi

    if [ -f "$ENRICHMENT_DIR/${target}-enriched.md" ]; then
        echo "$ENRICHMENT_DIR/${target}-enriched.md"
        return
    fi

    find "$ENRICHMENT_DIR" -maxdepth 1 -name "*${target}*.md" 2>/dev/null | sort -r | head -1
}

preview_markdown_file() {
    local file="$1"

    if command -v glow >/dev/null 2>&1; then
        glow -s dark "$file"
        return
    fi

    if command -v bat >/dev/null 2>&1; then
        bat --paging=never --language markdown "$file"
        return
    fi

    $PYTHON_CMD "$ECHOBOX_DIR/pipeline/markdown_preview.py" "$file"
}

read_config() {
    local key="$1"
    local default="$2"
    local val
    val=$($PYTHON_CMD "$ECHOBOX_DIR/pipeline/read_config.py" value "$CONFIG" "$key" "$default" 2>/dev/null)
    echo "${val:-$default}"
}

write_config_value() {
    local key="$1"
    local value="$2"
    "$PYTHON_CMD" - "$CONFIG" "$key" "$value" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).parent.parent))
from pipeline.fit import write_config_value

config_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

if not write_config_value(config_path, key, value):
    raise SystemExit(1)
PY
}

resolve_paths() {
    eval "$($PYTHON_CMD "$ECHOBOX_DIR/pipeline/read_config.py" paths "$CONFIG")"
    mkdir -p "$LOG_DIR" "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR" "$STATE_DIR"
}

# Fast-path for help (skip config parsing)
case "${1:-help}" in
    help|-h|--help) usage; exit 0 ;;
esac

resolve_paths

cmd_status() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/status.py" \
        "$CONFIG" "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR" "$LOG_DIR"
}

cmd_enrich() {
    local verbose=false
    while [ $# -gt 0 ]; do
        case "$1" in
            --verbose)
                verbose=true
                shift
                ;;
            *)
                break
                ;;
        esac
    done

    local transcript="$1"
    [ -n "$transcript" ] || die "provide a transcript file path or name. Usage: echobox enrich <transcript>"

    if [ ! -f "$transcript" ]; then
        local found="$TRANSCRIPT_DIR/${transcript}.txt"
        if [ ! -f "$found" ]; then
            found=$(find "$TRANSCRIPT_DIR" -name "*${transcript}*" -maxdepth 1 2>/dev/null | head -1)
        fi
        if [ -n "$found" ] && [ -f "$found" ]; then
            transcript="$found"
        fi
    fi
    require_file "$transcript" "transcript not found: $transcript. Run 'echobox list' to see available calls."

    local transcript_id
    transcript_id=$(basename "$transcript" .txt)
    local output="$ENRICHMENT_DIR/${transcript_id}-enriched.md"

    echo "Enriching: $transcript"
    echo "Output:    $output"
    echo ""

    WORKSTATION="${ECHOBOX_WORKSTATION:-$(read_config 'workstation_ssh' '')}"

    if [ -n "$WORKSTATION" ]; then
        local remote_basename
        remote_basename=$(basename "$transcript" | sed 's/[^a-zA-Z0-9._-]/_/g')
        local remote_enriched="${transcript_id}-enriched.md"
        remote_enriched=$(echo "$remote_enriched" | sed 's/[^a-zA-Z0-9._-]/_/g')

        echo "Syncing transcript to workstation..."
        rsync -az "$transcript" "$WORKSTATION:~/echobox-data/transcripts/$remote_basename"
        local remote_cmd="cd ~/echobox && python3 pipeline/enrich.py ~/echobox-data/transcripts/$remote_basename -o ~/echobox-data/enrichments/$remote_enriched"
        if [ "$verbose" = true ]; then
            remote_cmd="$remote_cmd --verbose"
        fi
        ssh -o ConnectTimeout=10 "$WORKSTATION" "$remote_cmd"
        rsync -az "$WORKSTATION:~/echobox-data/enrichments/$remote_enriched" "$output"
        rsync -az "$WORKSTATION:~/echobox-data/enrichments/${remote_enriched%.md}.json" "${output%.md}.json" 2>/dev/null || true
    else
        if [ "$verbose" = true ]; then
            $PYTHON_CMD "$ECHOBOX_DIR/pipeline/enrich.py" "$transcript" -o "$output" --verbose
        else
            $PYTHON_CMD "$ECHOBOX_DIR/pipeline/enrich.py" "$transcript" -o "$output"
        fi
    fi

    echo ""
    echo "Enrichment complete: $output"

    if [ -f "$output" ]; then
        local summary=$(grep -A 3 "^## Meeting Summary" "$output" 2>/dev/null | tail -n +2 | head -2 | tr '\n' ' ')
        local sidecar
        sidecar=$(json_sidecar_for_enrichment "$output")
        local action_count=0
        local speaker_count=0
        if [ -n "$sidecar" ]; then
            action_count=$($PYTHON_CMD -c "import json,sys; print(len(json.load(open(sys.argv[1])).get('action_items', [])))" "$sidecar" 2>/dev/null || echo 0)
            speaker_count=$($PYTHON_CMD -c "import json,sys; print(len(json.load(open(sys.argv[1])).get('speakers', [])))" "$sidecar" 2>/dev/null || echo 0)
        fi

        if [ -n "$summary" ]; then
            echo ""
            echo "  Summary: $(echo "$summary" | cut -c1-120)"
        fi
        if [ "$action_count" -gt 0 ] 2>/dev/null; then
            echo "  Action items: $action_count"
        fi
        if [ "$speaker_count" -gt 0 ] 2>/dev/null; then
            echo "  Speakers: $speaker_count"
        fi
        echo ""
        echo "  Preview: ./echobox.sh preview $output"
        echo "  Next: ./echobox.sh publish $output"
        echo "    Or: ./echobox.sh open (after publish)"
    fi
}

cmd_publish() {
    local enrichment="$1"
    [ -n "$enrichment" ] || die "provide an enrichment file path. Usage: echobox publish <enrichment.md>"
    require_file "$enrichment" "enrichment not found: $enrichment"

    echo "Publishing: $enrichment"
    bash "$ECHOBOX_DIR/pipeline/publish.sh" "$enrichment"
}

cmd_preview() {
    local target="$1"
    local enrichment
    enrichment=$(resolve_enrichment_input "$target")

    if [ -z "$enrichment" ]; then
        if [ -n "$target" ]; then
            echo "No enrichment matched: $target"
        else
            echo "No enrichments found yet."
        fi
        echo "Run: ./echobox.sh enrich <transcript.txt>"
        exit 1
    fi

    echo "Previewing: $enrichment"
    echo ""
    preview_markdown_file "$enrichment"
}

cmd_quality() {
    echo "Echobox Quality Report"
    echo "======================"
    echo ""

    echo "--- Pipeline Check ---"
    bash "$ECHOBOX_DIR/quality/pipeline-check.sh"
    echo ""

    echo "--- Context Check ---"
    bash "$ECHOBOX_DIR/quality/context-check.sh"
}

cmd_watch() {
    echo "Starting Echobox watcher..."
    echo "Transcripts will be saved to: $TRANSCRIPT_DIR"
    echo "Press Ctrl+C to stop."
    echo ""

    HOOK_CMD="bash $ECHOBOX_DIR/pipeline/orchestrator.sh"

    if command -v trnscrb &>/dev/null; then
        trnscrb watch \
            --output-dir "$TRANSCRIPT_DIR" \
            --on-stop "$HOOK_CMD {transcript_id}" \
            2>&1 | tee -a "$LOG_DIR/watcher.log"
    else
        echo "Error: trnscrb not found."
        echo "  Install: brew install ramiloif/tap/trnscrb"
        exit 1
    fi
}

cmd_list() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/list_calls.py" "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR"
}

cmd_actions() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/actions.py" "$ENRICHMENT_DIR"
}

cmd_summary() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/summary.py" "$ENRICHMENT_DIR" "$@"
}

cmd_reprocess() {
    local name="$1"
    if [ -z "$name" ]; then
        echo "Usage: echobox reprocess <call-name>"
        echo ""
        echo "Re-enriches a transcript and publishes a new report."
        echo "Use 'echobox list' to see available calls."
        exit 1
    fi

    local transcript="$TRANSCRIPT_DIR/${name}.txt"
    if [ ! -f "$transcript" ]; then
        transcript=$(find "$TRANSCRIPT_DIR" -name "*${name}*" -maxdepth 1 2>/dev/null | head -1)
    fi

    if [ ! -f "$transcript" ]; then
        echo "Error: no transcript matching '$name'"
        echo "Available:"
        ls "$TRANSCRIPT_DIR"/*.txt 2>/dev/null | while read -r f; do
            echo "  $(basename "$f" .txt)"
        done
        exit 1
    fi

    local base=$(basename "$transcript" .txt)
    local enrichment="$ENRICHMENT_DIR/${base}-enriched.md"

    echo "Reprocessing: $base"
    echo ""

    echo "[1/2] Enriching..."
    cmd_enrich "$transcript"

    echo "[2/2] Publishing..."
    bash "$ECHOBOX_DIR/pipeline/publish.sh" "$enrichment"

    echo ""
    echo "Done."
    echo "  Preview:  echobox preview $base"
    echo "  Report:   echobox open"
}

cmd_setup() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/setup.py" \
        "$CONFIG" "$ECHOBOX_DIR/config/echobox.example.yaml"
}

cmd_search() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/search.py" \
        "$1" "$ENRICHMENT_DIR" "$TRANSCRIPT_DIR"
}

_try_open() {
    local file="$1"
    if open "$file" 2>/dev/null; then return 0; fi
    echo "  File: $file"
    echo "  No browser available. To view the enrichment:"
    echo "    echobox preview"
    echo "  Or copy the HTML to a machine with a browser."
    return 1
}

cmd_open() {
    local target="$1"

    if [ -z "$target" ]; then
        local latest=$(find "$REPORT_DIR" -name "report.html" 2>/dev/null | sort -r | head -1)
        if [ -n "$latest" ]; then
            echo "Opening: $latest"
            _try_open "$latest"
            return
        fi

        local latest_url=$(cat "$STATE_DIR/last-report-url" 2>/dev/null)
        if [ -n "$latest_url" ] && [[ "$latest_url" == http* ]]; then
            echo "Opening: $latest_url"
            open "$latest_url" 2>/dev/null || echo "  URL: $latest_url"
            return
        fi

        echo "No reports found."
        echo "  Run: echobox enrich <transcript> && echobox publish <enrichment>"
        exit 1
    fi

    if [ -f "$target" ]; then
        _try_open "$target"
    elif [ -f "$REPORT_DIR/$target/report.html" ]; then
        _try_open "$REPORT_DIR/$target/report.html"
    else
        echo "Report not found: $target"
        local available=$(find "$REPORT_DIR" -name "report.html" 2>/dev/null)
        if [ -n "$available" ]; then
            echo "Available:"
            echo "$available" | while read -r f; do echo "  $(dirname "$f" | xargs basename)"; done
        fi
        exit 1
    fi
}

cmd_clean() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/clean.py" "$DATA_DIR" "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR" "$LOG_DIR" "$@"
}

cmd_config() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/show_config.py" "$CONFIG"
}

cmd_test() {
    echo "Echobox Smoke Tests"
    echo "==================="
    echo ""
    local test_dir="$ECHOBOX_DIR/tests"
    local failures=0
    for test_file in "$test_dir"/test_*.py; do
        [ -f "$test_file" ] || continue
        name=$(basename "$test_file" .py)
        if $PYTHON_CMD "$test_file" >/dev/null 2>&1; then
            echo "  [ok] $name"
        else
            echo "  [FAIL] $name"
            failures=$((failures + 1))
        fi
    done
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo "All tests passed."
    else
        echo "$failures test(s) failed."
        exit 1
    fi
}

cmd_fit() {
    $PYTHON_CMD "$ECHOBOX_DIR/pipeline/fit.py" "$@" --config "$CONFIG"
}

cmd_demo() {
    "$PYTHON_CMD" "$ECHOBOX_DIR/pipeline/demo.py" "$ECHOBOX_DIR" "$CONFIG" "$REPORT_DIR" open
}

case "${1:-help}" in
    list|ls)     cmd_list ;;
    search)      cmd_search "$2" ;;
    open)        cmd_open "$2" ;;
    preview)     cmd_preview "$2" ;;
    actions)     cmd_actions ;;
    summary)     shift; cmd_summary "$@" ;;
    reprocess)   cmd_reprocess "$2" ;;
    setup)       cmd_setup ;;
    status)      cmd_status ;;
    enrich)      shift; cmd_enrich "$@" ;;
    publish)     cmd_publish "$2" ;;
    quality) cmd_quality ;;
    watch)   cmd_watch ;;
    fit)     shift; cmd_fit "$@" ;;
    clean)   shift; cmd_clean "$@" ;;
    config)  cmd_config ;;
    demo)    cmd_demo ;;
    test)    cmd_test ;;
    version|-v|--version) cmd_version ;;
    help|-h|--help) usage ;;
    *)
        echo "Unknown command: $1"
        echo ""
        usage
        exit 1
        ;;
esac
