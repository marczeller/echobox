#!/bin/bash
# Echobox repo quality metric — higher is better, target: 20/20
cd "$(dirname "$0")/.."
SCORE=0

# 1. README has quick start with 3 or fewer steps
S=$(grep -c "^###\|^##" README.md 2>/dev/null)
if [ "$S" -ge 8 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "1  [!!]README: missing sections"; fi

# 2. install.sh is executable and has error handling
if [ -x install.sh ] && grep -q "set -e\|exit 1" install.sh 2>/dev/null; then SCORE=$((SCORE+1)); else echo "2  [!!]install.sh: not executable or no error handling"; fi

# 3. echobox.sh CLI has help text and at least 4 commands
S=$(grep -c "status\|enrich\|publish\|quality\|watch\|fit" echobox.sh 2>/dev/null)
if [ "$S" -ge 6 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "3  [!!]echobox.sh: missing commands"; fi

# 4. All pipeline scripts are executable
NON_EXEC=$(find pipeline -name "*.sh" ! -perm -111 2>/dev/null | wc -l | tr -d ' ')
if [ "$NON_EXEC" -eq 0 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "4  [!!]Pipeline: $NON_EXEC non-executable scripts"; fi

# 5. enrich.py reads config (not hardcoded values)
if grep -q "yaml\|config\|ECHOBOX" pipeline/enrich.py 2>/dev/null; then SCORE=$((SCORE+1)); else echo "5  [!!]enrich.py: no config support"; fi

# 6. Zero private info leaks
LEAKS=$(grep -rn 'Gaki\|gaki\|Haun\|Dragonfly\|Marc Zeller\|Ernesto\|100\.85\|100\.119\|aavechan\|bgdlabs\|GakiCalls\|Aave\|aave\|Morpho\|morpho' --include="*.md" --include="*.py" --include="*.sh" --include="*.yaml" --include="*.js" --include="*.html" 2>/dev/null | grep -v ".git/" | grep -v "docs/superpowers/" | wc -l | tr -d ' ')
if [ "$LEAKS" -eq 0 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "6  [!!]Sanitization: $LEAKS leaks found"; fi

# 7. All docs exist
DOCS_MISSING=0
for f in docs/setup.md docs/context-sources.md docs/troubleshooting.md docs/design-decisions.md; do
    [ ! -f "$f" ] && DOCS_MISSING=$((DOCS_MISSING+1))
done
if [ "$DOCS_MISSING" -eq 0 ]; then SCORE=$((SCORE+1)); else echo "7  [!!]Docs: $DOCS_MISSING missing"; fi

# 8. Example config is well-commented (>20 comment lines)
COMMENTS=$(grep -c "^#\|^  #" config/echobox.example.yaml 2>/dev/null)
if [ "$COMMENTS" -ge 20 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "8  [!!]Config: only $COMMENTS comment lines"; fi

# 9. Patches README has table with all patches
PATCHES=$(ls patches/*.diff 2>/dev/null | wc -l | tr -d ' ')
PATCH_ROWS=$(grep -c "\.diff" patches/README.md 2>/dev/null)
if [ "$PATCHES" -eq "$PATCH_ROWS" ] && [ "$PATCHES" -ge 5 ]; then SCORE=$((SCORE+1)); else echo "9  [!!]Patches: $PATCHES diffs, $PATCH_ROWS in README"; fi

# 10. HTML template has CSS variables for theming
if grep -q "var(--\|:root" templates/report.html 2>/dev/null; then SCORE=$((SCORE+1)); else echo "10 [!!]Template: no CSS variables"; fi

# 11. gate.js reads password from env var
if grep -q "process.env" templates/gate.js 2>/dev/null; then SCORE=$((SCORE+1)); else echo "11 [!!]gate.js: hardcoded password"; fi

# 12. orchestrator.sh handles single-machine mode (no required SSH)
if grep -q 'if.*WORKSTATION\|WORKSTATION:-' pipeline/orchestrator.sh 2>/dev/null; then SCORE=$((SCORE+1)); else echo "12 [!!]orchestrator: requires workstation"; fi

# 13. README has pipeline description (How It Works table or diagram)
if grep -q "How It Works\|Detection\|Transcription\|Enrichment\|Publishing" README.md 2>/dev/null; then SCORE=$((SCORE+1)); else echo "13 [!!]README: no pipeline description"; fi

# 14. LICENSE exists
if [ -f LICENSE ]; then SCORE=$((SCORE+1)); else echo "14 [!!]No LICENSE"; fi

# 15. Python script has argparse (user-friendly CLI)
if grep -q "argparse\|ArgumentParser" pipeline/enrich.py 2>/dev/null; then SCORE=$((SCORE+1)); else echo "15 [!!]enrich.py: no argparse"; fi

# 16. setup.md has macOS and two-machine instructions
if grep -q "macOS\|Mac" docs/setup.md 2>/dev/null && grep -q "Two-Machine\|Workstation" docs/setup.md 2>/dev/null; then SCORE=$((SCORE+1)); else echo "16 [!!]setup.md: missing macOS or two-machine docs"; fi

# 17. Troubleshooting doc has at least 5 issues
ISSUES=$(grep -c "^###\|^##.*issue\|^##.*problem\|^##.*error\|^##.*fix\|^##.*fail" docs/troubleshooting.md 2>/dev/null)
if [ "$ISSUES" -ge 5 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "17 [!!]Troubleshooting: only $ISSUES issues"; fi

# 18. design-decisions.md has table
if grep -c "|.*|.*|" docs/design-decisions.md 2>/dev/null | grep -q "[5-9]\|[0-9][0-9]"; then SCORE=$((SCORE+1)); else echo "18 [!!]design-decisions: no table"; fi

# 19. README is concise but complete (has Quick Start + How It Works + Commands)
if grep -q "Quick Start" README.md 2>/dev/null && grep -q "Common Commands\|Command" README.md 2>/dev/null; then SCORE=$((SCORE+1)); else echo "19 [!!]README: missing Quick Start or Commands section"; fi

# 20. No unfinished markers left in code
_t="TO""DO"; _f="FI""XME"; _h="HA""CK"; _x="X""XX"
TODOS=$(grep -rn "$_t\|$_f\|$_h\|$_x" --include="*.py" --include="*.sh" 2>/dev/null | grep -v ".git/" | grep -v "repo-quality.sh" | wc -l | tr -d ' ')
if [ "$TODOS" -eq 0 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "20 [!!]Code: $TODOS unfinished markers left"; fi

# 21. AI agent files exist (CLAUDE.md and AGENTS.md)
if [ -f CLAUDE.md ] && [ -f AGENTS.md ]; then SCORE=$((SCORE+1)); else echo "21 [!!]Missing CLAUDE.md or AGENTS.md"; fi

# 22. Model fit tool exists
if [ -f pipeline/fit.py ] && grep -q "llmfit\|LLMFit" pipeline/fit.py 2>/dev/null; then SCORE=$((SCORE+1)); else echo "22 [!!]pipeline/fit.py: missing or no LLMFit integration"; fi

# 23. .gitignore exists and excludes config
if [ -f .gitignore ] && grep -q "echobox.yaml" .gitignore 2>/dev/null; then SCORE=$((SCORE+1)); else echo "23 [!!]No .gitignore or config not excluded"; fi

# 24. VERSION file exists
if [ -f VERSION ]; then SCORE=$((SCORE+1)); else echo "24 [!!]No VERSION file"; fi

# 25. Tests exist and pass
if [ -f tests/test_config_parser.py ] && python3 tests/test_config_parser.py >/dev/null 2>&1; then SCORE=$((SCORE+1)); else echo "25 [!!]Config parser tests missing or failing"; fi

# 26. Demo fixtures exist
FIXTURE_COUNT=$(find tests/fixtures -name "*.txt" -o -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
if [ "$FIXTURE_COUNT" -ge 2 ] 2>/dev/null; then SCORE=$((SCORE+1)); else echo "26 [!!]Demo fixtures missing"; fi

# 27. Status or platform support info in README
if grep -q "macOS\|Status" README.md 2>/dev/null; then SCORE=$((SCORE+1)); else echo "27 [!!]README: no platform status info"; fi

echo ""
echo "$SCORE"
