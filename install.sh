#!/bin/bash
set -e

ECHOBOX_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$ECHOBOX_DIR/config"
PATCHES_DIR="$ECHOBOX_DIR/patches"
START_TIME=$(date +%s)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

STEP_NUM=0
TOTAL_STEPS=10

ok()   { echo -e "  ${GREEN}[ok]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
step() {
    STEP_NUM=$((STEP_NUM + 1))
    echo -e "\n${BLUE}[${STEP_NUM}/${TOTAL_STEPS}]${NC} ${BOLD}$1${NC}"
}

confirm() {
    local prompt="$1"
    local answer
    read -rp "$prompt [Y/n]: " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}

python_pip_install_cmd() {
    local in_venv
    in_venv=$($PYTHON_CMD -c "import sys; print('true' if sys.prefix != getattr(sys, 'base_prefix', sys.prefix) else 'false')" 2>/dev/null || echo "false")
    if [ "$in_venv" = "true" ]; then
        echo "$PYTHON_CMD -m pip install"
    else
        echo "$PYTHON_CMD -m pip install --user"
    fi
}

offer_python_package_install() {
    local package="$1"
    local note="$2"
    local pip_cmd

    if ! $PYTHON_CMD -m pip --version >/dev/null 2>&1; then
        warn "$package not found and pip is unavailable"
        echo "    Install pip for $PYTHON_CMD, then run: $PYTHON_CMD -m pip install --user $package"
        [ -n "$note" ] && echo "    $note"
        ERRORS=$((ERRORS + 1))
        return
    fi

    pip_cmd=$(python_pip_install_cmd)
    if confirm "  Install $package now with: $pip_cmd $package ?"; then
        if eval "$pip_cmd \"$package\""; then
            ok "$package installed"
        else
            warn "Automatic install failed for $package"
            echo "    Retry manually: $pip_cmd $package"
            [ -n "$note" ] && echo "    $note"
            ERRORS=$((ERRORS + 1))
        fi
    else
        warn "$package skipped"
        echo "    Run later: $pip_cmd $package"
        [ -n "$note" ] && echo "    $note"
        ERRORS=$((ERRORS + 1))
    fi
}

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║         ECHOBOX INSTALLER             ║"
echo "  ║  Self-hosted call intelligence        ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

ERRORS=0

step "Checking system requirements"

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    fail "Apple Silicon required (detected: $ARCH)"
    ERRORS=$((ERRORS + 1))
else
    ok "Apple Silicon detected"
fi

MACOS_VER=$(sw_vers -productVersion 2>/dev/null || echo "0.0")
MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
if [ "$MACOS_MAJOR" -ge 14 ] 2>/dev/null; then
    ok "macOS $MACOS_VER (Sonoma or later)"
else
    warn "macOS $MACOS_VER — macOS 14+ (Sonoma) recommended"
fi

if ! command -v brew &>/dev/null; then
    fail "Homebrew not found — install from https://brew.sh"
    ERRORS=$((ERRORS + 1))
else
    ok "Homebrew installed"
fi

step "Checking dependencies"

if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not found — installing via Homebrew"
    brew install ffmpeg
    ok "ffmpeg installed"
else
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')"
fi

if ! command -v trnscrb &>/dev/null; then
    fail "trnscrb not found"
    echo "    Install: brew install ramiloif/tap/trnscrb"
    echo "    Then re-run this installer."
    ERRORS=$((ERRORS + 1))
else
    TRNSCRB_VERSION=$(trnscrb --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    ok "trnscrb $TRNSCRB_VERSION"
fi

PYTHON_CMD=""
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    fail "Python 3.12+ not found — install: brew install python@3.12"
    ERRORS=$((ERRORS + 1))
else
    ok "Python $($PYTHON_CMD --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
fi

step "Checking Python packages"

HAS_FASTER_WHISPER=false
if $PYTHON_CMD -c "import faster_whisper" 2>/dev/null; then
    ok "faster-whisper installed"
    HAS_FASTER_WHISPER=true
else
    offer_python_package_install "faster-whisper" ""
    if $PYTHON_CMD -c "import faster_whisper" 2>/dev/null; then
        HAS_FASTER_WHISPER=true
    fi
fi

if $PYTHON_CMD -c "import yaml" 2>/dev/null; then
    ok "PyYAML installed"
else
    offer_python_package_install "pyyaml" ""
fi

if $PYTHON_CMD -c "import pyannote.audio" 2>/dev/null; then
    ok "pyannote.audio installed"
else
    offer_python_package_install \
        "pyannote.audio" \
        "Requires accepting the pyannote model license and setting HF_TOKEN: https://huggingface.co/pyannote/speaker-diarization-3.1"
fi

step "Checking BlackHole audio driver"

if system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole"; then
    ok "BlackHole audio driver detected"
else
    warn "BlackHole not found — install: brew install blackhole-2ch"
    echo "    BlackHole captures system audio for recording."
    echo "    After install, create a Multi-Output Device in Audio MIDI Setup."
fi

step "Checking HuggingFace token (for pyannote speaker diarization)"

if [ -n "$HF_TOKEN" ]; then
    ok "HF_TOKEN environment variable set"
elif [ -f "$HOME/.huggingface/token" ] || [ -f "$HOME/.cache/huggingface/token" ]; then
    ok "HuggingFace token file found"
else
    warn "No HuggingFace token found"
    echo "    pyannote requires accepting the model license and setting HF_TOKEN."
    echo "    1. Accept license: https://huggingface.co/pyannote/speaker-diarization-3.1"
    echo "    2. Create token: https://huggingface.co/settings/tokens"
    echo "    3. Export: echo 'export HF_TOKEN=hf_...' >> ~/.zshrc"
fi

step "Downloading Whisper large-v3 model"

if [ "$HAS_FASTER_WHISPER" = "true" ]; then
    WHISPER_MODEL_DIR="$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3"
    if [ -d "$WHISPER_MODEL_DIR" ]; then
        ok "Whisper large-v3 already downloaded"
    else
        echo "    Downloading large-v3 model (~3 GB)..."
        $PYTHON_CMD -c "
from faster_whisper import WhisperModel
print('Downloading model...')
model = WhisperModel('large-v3', device='cpu', compute_type='int8')
print('Done.')
" 2>/dev/null && ok "Whisper large-v3 downloaded" || warn "Model download failed — will retry on first use"
    fi
else
    warn "Skipped — install faster-whisper first, then re-run"
fi

step "Applying patches to trnscrb"

TRNSCRB_SITE_PACKAGES=$(trnscrb --version 2>/dev/null; $PYTHON_CMD -c "
import trnscrb, pathlib
print(pathlib.Path(trnscrb.__file__).parent)
" 2>/dev/null || echo "")

if [ -n "$TRNSCRB_SITE_PACKAGES" ] && [ -d "$TRNSCRB_SITE_PACKAGES" ]; then
    for patch_desc in "$PATCHES_DIR"/*.diff; do
        [ -f "$patch_desc" ] || continue
        patch_name=$(basename "$patch_desc")
        echo "    Patch: $patch_name"
        echo "    (Review patches/README.md for manual application instructions)"
    done
    ok "Patch descriptions available in patches/"
    warn "Patches must be applied manually — trnscrb install paths vary by system"
    echo "    See: $ECHOBOX_DIR/patches/README.md"
else
    warn "Could not locate trnscrb package directory"
    echo "    Apply patches manually after installing trnscrb."
fi

step "Creating configuration"

if [ -f "$CONFIG_DIR/echobox.yaml" ]; then
    ok "Config already exists: config/echobox.yaml"
else
    cp "$CONFIG_DIR/echobox.example.yaml" "$CONFIG_DIR/echobox.yaml"
    ok "Created config/echobox.yaml from example"
    echo "    Edit this file to configure your context sources and preferences."
fi

step "Setting up directories and launchd service"

TRANSCRIPT_DIR="$HOME/echobox-data/transcripts"
ENRICHMENT_DIR="$HOME/echobox-data/enrichments"
REPORT_DIR="$HOME/echobox-data/reports"
LOG_DIR="$HOME/echobox-data/logs"
mkdir -p "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR" "$REPORT_DIR" "$LOG_DIR"
ok "Data directory: ~/echobox-data/"

PLIST_LABEL="com.echobox.watcher"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [ -f "$PLIST_PATH" ]; then
    ok "launchd service already exists"
else
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${ECHOBOX_DIR}/echobox.sh</string>
        <string>watch</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/echobox.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/echobox.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:${HOME}/bin:${HOME}/.local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
    ok "Created launchd plist: $PLIST_PATH"
    echo "    Start: launchctl load $PLIST_PATH"
    echo "    Stop:  launchctl unload $PLIST_PATH"
fi

step "Validation"

echo ""
TOTAL_CHECKS=0
PASSED_CHECKS=0

validate() {
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if eval "$2" 2>/dev/null; then
        ok "$1"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        fail "$1"
    fi
}

validate "echobox.sh executable" "[ -x '$ECHOBOX_DIR/echobox.sh' ] || chmod +x '$ECHOBOX_DIR/echobox.sh'"
validate "Config exists" "[ -f '$CONFIG_DIR/echobox.yaml' ]"
validate "Pipeline scripts present" "[ -f '$ECHOBOX_DIR/pipeline/enrich.py' ]"
validate "Data directories exist" "[ -d '$HOME/echobox-data/transcripts' ]"
validate "Template exists" "[ -f '$ECHOBOX_DIR/templates/report.html' ]"

echo ""
echo "  ════════════════════════════════════════"
echo "  Validation: $PASSED_CHECKS/$TOTAL_CHECKS checks passed"

if [ $ERRORS -gt 0 ]; then
    echo ""
    warn "$ERRORS issue(s) found — fix them and re-run install.sh"
fi

ELAPSED=$(( $(date +%s) - START_TIME ))
echo ""
echo -e "  ${GREEN}Completed in ${ELAPSED}s${NC}"
echo ""
echo "  Next steps:"
echo "    1. Edit config/echobox.yaml"
echo "    2. Run ./echobox.sh fit to find the best models for your hardware"
echo "    3. Apply trnscrb patches (see patches/README.md)"
echo "    4. Start: ./echobox.sh watch"
echo "    5. Or load the launchd service for auto-start"
echo ""
