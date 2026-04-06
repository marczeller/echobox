#!/bin/bash
set -e

ECHOBOX_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$ECHOBOX_DIR/config"
START_TIME=$(date +%s)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

STEP_NUM=0
TOTAL_STEPS=9

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

confirm_default_no() {
    local prompt="$1"
    local answer
    read -rp "$prompt [y/N]: " answer
    [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
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
CONFIG_CREATED=false
FIT_RAN=false
FIT_SKIPPED=false

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
    HAS_BREW=false
else
    ok "Homebrew installed"
    HAS_BREW=true
fi

step "Checking dependencies"

if ! command -v ffmpeg &>/dev/null; then
    if [ "$HAS_BREW" = "true" ]; then
        warn "ffmpeg not found — installing via Homebrew"
        brew install ffmpeg
        ok "ffmpeg installed"
    else
        fail "ffmpeg not found — install Homebrew first, then run: brew install ffmpeg"
        ERRORS=$((ERRORS + 1))
    fi
else
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')"
fi

PYTHON_CMD=""
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' || true)
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        [ -z "$PY_MAJOR" ] && continue
        [ -z "$PY_MINOR" ] && continue
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

    if [ ! -d "$ECHOBOX_DIR/.venv" ]; then
        echo "    Creating virtual environment in $ECHOBOX_DIR/.venv"
        "$PYTHON_CMD" -m venv "$ECHOBOX_DIR/.venv"
    fi
    PYTHON_CMD="$ECHOBOX_DIR/.venv/bin/python"
    "$PYTHON_CMD" -m pip install --upgrade pip >/dev/null 2>&1
    ok "Using venv: $ECHOBOX_DIR/.venv"
fi

step "Checking Python packages"

if [ -z "$PYTHON_CMD" ]; then
    warn "Skipped — install Python 3.12+ first: brew install python@3.12"
    ERRORS=$((ERRORS + 1))
else

HAS_MLX_WHISPER=false
if $PYTHON_CMD -c "import mlx_whisper" 2>/dev/null; then
    ok "mlx-whisper installed"
    HAS_MLX_WHISPER=true
else
    offer_python_package_install "mlx-whisper" ""
    if $PYTHON_CMD -c "import mlx_whisper" 2>/dev/null; then
        HAS_MLX_WHISPER=true
    fi
fi

if $PYTHON_CMD -c "import yaml" 2>/dev/null; then
    ok "PyYAML installed"
else
    offer_python_package_install "pyyaml" ""
fi

if $PYTHON_CMD -c "import sounddevice" 2>/dev/null; then
    ok "sounddevice installed"
else
    offer_python_package_install "sounddevice" ""
fi

if $PYTHON_CMD -c "import pyannote.audio" 2>/dev/null; then
    ok "pyannote.audio installed"
else
    offer_python_package_install \
        "pyannote.audio" \
        "Requires accepting the pyannote model license and setting HF_TOKEN: https://huggingface.co/pyannote/speaker-diarization-3.1"
fi

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

step "Checking Whisper model cache"

if [ -z "$PYTHON_CMD" ]; then
    warn "Skipped — install Python 3.12+ first: brew install python@3.12"
elif [ "$HAS_MLX_WHISPER" = "true" ]; then
    WHISPER_MODEL_DIR="$HOME/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx"
    if [ -d "$WHISPER_MODEL_DIR" ]; then
        ok "Whisper large-v3 MLX model already cached"
    else
        warn "Whisper large-v3 MLX model not cached yet"
        echo "    mlx-whisper downloads models on first transcription or fit run."
        echo "    Expected cache path: $WHISPER_MODEL_DIR"
    fi
else
    warn "Skipped — install mlx-whisper first, then re-run"
fi

step "Creating configuration"

if [ -f "$CONFIG_DIR/echobox.yaml" ]; then
    ok "Config already exists: config/echobox.yaml"
else
    cp "$CONFIG_DIR/echobox.example.yaml" "$CONFIG_DIR/echobox.yaml"
    CONFIG_CREATED=true
    ok "Created config/echobox.yaml from example"
    echo "    Edit this file to configure your context sources and preferences."
fi

step "Selecting models for this machine"

if [ -z "$PYTHON_CMD" ]; then
    warn "Skipped model fit — Python 3.12+ is required"
    FIT_SKIPPED=true
elif [ ! -x "$ECHOBOX_DIR/echobox" ]; then
    warn "Skipped model fit — echobox CLI is not executable yet"
    FIT_SKIPPED=true
else
    HAS_WHISPER_MODEL=false
    HAS_MLX_MODEL=false

    if grep -Eq '^whisper_model:[[:space:]]*[^#[:space:]].*$' "$CONFIG_DIR/echobox.yaml"; then
        HAS_WHISPER_MODEL=true
    fi
    if grep -Eq '^mlx_model:[[:space:]]*[^#[:space:]].*$' "$CONFIG_DIR/echobox.yaml"; then
        HAS_MLX_MODEL=true
    fi

    SHOULD_RUN_FIT=false
    if [ "$CONFIG_CREATED" = "true" ] || [ "$HAS_WHISPER_MODEL" = "false" ] || [ "$HAS_MLX_MODEL" = "false" ]; then
        SHOULD_RUN_FIT=true
    elif confirm_default_no "  Re-run hardware fit to re-check models for this Mac?"; then
        SHOULD_RUN_FIT=true
    else
        ok "Keeping existing whisper_model and mlx_model settings"
    fi

    if [ "$SHOULD_RUN_FIT" = "true" ]; then
        echo "    Running ./echobox fit --auto"
        if "$ECHOBOX_DIR/echobox" fit --auto; then
            FIT_RAN=true
            ok "Model fit completed"
        else
            FIT_SKIPPED=true
            warn "Model fit failed — run ./echobox fit after fixing the issue above"
        fi
    fi
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
        <string>/bin/bash</string>
        <string>${ECHOBOX_DIR}/scripts/run-echobox.sh</string>
        <string>${ECHOBOX_DIR}/echobox</string>
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

validate "echobox executable" "[ -x '$ECHOBOX_DIR/echobox' ] || chmod +x '$ECHOBOX_DIR/echobox'"
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
echo "    1. Review config/echobox.yaml"
if [ "$FIT_SKIPPED" = "true" ] && [ "$FIT_RAN" = "false" ]; then
    echo "    2. Run ./echobox fit after the missing dependency or error is fixed"
    echo "    3. Start your MLX server with the configured mlx_model"
    echo "    4. Run ./echobox status and ./echobox demo"
    echo "    5. Start: ./echobox watch"
    echo "    6. Or load the launchd service for auto-start"
else
    echo "    2. Start your MLX server with the configured mlx_model"
    echo "    3. Run ./echobox status and ./echobox demo"
    echo "    4. Start: ./echobox watch"
    echo "    5. Or load the launchd service for auto-start"
fi
echo ""
