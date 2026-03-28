#!/bin/bash
# Shared Python discovery for pipeline scripts.
# Source this file: . "$ECHOBOX_DIR/pipeline/python.sh"

if [ -z "$ECHOBOX_PYTHON" ]; then
    ECHOBOX_PYTHON=""
    for cmd in python3.12 python3; do
        if command -v "$cmd" &>/dev/null; then
            PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
            PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
            PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
            if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ] 2>/dev/null; then
                ECHOBOX_PYTHON="$cmd"
                break
            fi
        fi
    done
    ECHOBOX_PYTHON="${ECHOBOX_PYTHON:-python3}"
    export ECHOBOX_PYTHON
fi
