#!/bin/bash
# Ingest call artifacts from recording machine to workstation
# Run on the workstation to pull transcripts and enrichments.

set -e

DATA_DIR="$HOME/echobox-data"
TRANSCRIPT_DIR="$DATA_DIR/transcripts"
ENRICHMENT_DIR="$DATA_DIR/enrichments"

mkdir -p "$TRANSCRIPT_DIR" "$ENRICHMENT_DIR"

LAPTOP="${ECHOBOX_LAPTOP:-}"

if [ -z "$LAPTOP" ]; then
    echo "Error: ECHOBOX_LAPTOP not set"
    echo "Set it to the SSH target of your recording machine:"
    echo "  export ECHOBOX_LAPTOP=user@laptop.local"
    exit 1
fi

echo "Ingesting from $LAPTOP..."

rsync -az --update \
    "$LAPTOP:~/echobox-data/transcripts/" \
    "$TRANSCRIPT_DIR/"

rsync -az --update \
    "$LAPTOP:~/echobox-data/enrichments/" \
    "$ENRICHMENT_DIR/"

echo "Ingested call artifacts from $LAPTOP"
echo ""
echo "Recent transcripts:"
ls -lt "$TRANSCRIPT_DIR"/*.txt 2>/dev/null | head -5
echo ""
echo "Recent enrichments:"
ls -lt "$ENRICHMENT_DIR"/*.md 2>/dev/null | head -5
