# AI-Slop Scrub Assessment

Scope: every `.py` and `.sh` file in the repo (echobox CLI, `echobox_recorder/`, `pipeline/`, `quality/`, `scripts/`, `tests/`, `install.sh`).

Verdict: codebase is unusually clean. No process-narration ("# was X, now Y", "# renamed from"), no AI thought-process comments ("# Let's try"), no orphan TODO/FIXME/HACK markers, no stub functions with `pass` / `NotImplementedError` lacking callers. The genuine slop yield was modest: decorative section dividers, a few WHAT-not-WHY restatements, and one batch of dead code in `pipeline/fit.py`.

## Removals

### `echobox_recorder/caption_panel.py` (8 lines removed)

Removed 5 decorative section dividers and 3 single-word labels that restate the next code block:

| Line | Removed |
|---|---|
| 53 | `# ------------------------------------------------------------------ setup` |
| 114 | `# ---------------------------------------------------------------- display` |
| 146 | `# -------------------------------------------------------- event ingestion` |
| 178 | `# ---------------------------------------------------------------- drawing` |
| 238 | `# ------------------------------------------------------- main-thread util` |
| 190 | `# Status pill.` |
| 205 | `# Final text.` |
| 219 | `# Trailing partial.` |

Kept: docstring at top, "Critical: hide from screen-share recordings" (security WHY), "Also make the panel collectable across spaces" (WHY), "New final consumes the partial prefix" (state-machine WHY).

### `echobox_recorder/menubar.py` (8 dividers + WHAT comments removed; 1 comment rewritten)

Removed 7 decorative `# --- Section ---` dividers (Polling, UI updates, Folder actions, Recent items, Disk status, Routing status, Voices submenu, Quit) and three WHAT-not-WHY restatements.

| Line | Removed |
|---|---|
| 162 | `# --- Polling in background thread to avoid blocking AppKit ---` |
| 261 | `# --- UI updates ---` |
| 318 | `# --- Folder actions ---` |
| 326 | `# --- Recent items ---` |
| 381 | `# --- Disk status ---` |
| 448 | `# --- Routing status (BlackHole health check) ---` |
| 461 | `# --- Voices submenu ---` |
| 579 | `# --- Quit ---` |
| 170 | `# Check if the background poll changed state and update UI accordingly.` |
| 185 | `# Run the housekeeping sweep on its own longer cadence.` |
| 190 | `# Kick off next poll in background if not already running` |
| 511 | `# File picker` |

Rewritten:
- Before: `# _tick fires on the main AppKit thread — safe for UI updates.`
- After: `# Runs on the main AppKit thread — safe for UI mutation.`

Kept: SIGTERM handler (launchd specifics), activity-timer reset comments (race WHY), snapshot-before-stop comment (lifecycle WHY), legacy-location sweep comment (compat WHY), "Hide the item by setting an empty title when routing is fine" (UI convention).

### `echobox_recorder/recorder.py` (5 lines removed)

| Line | Removed |
|---|---|
| 207 | `# Check if a Multi-Output Device exists` (WHAT, restates next code) |
| 559 | `# If recording via BlackHole, ensure system output routes through it` (WHAT) |
| 835 | `# --- Pass 1: Consecutive repetition filter ---` (decorative; algorithm description on next line preserved) |
| 851 | `# --- Pass 2: Internal repetition filter ---` (decorative; description preserved) |
| 863 | `# --- Pass 3: Sliding window dedup ---` (decorative; description preserved) |

Kept: BlackHole / SwitchAudioSource WHY comments, AirPods rate-ladder docstrings, pinned model SHA security comment, every per-pass algorithm description, attribution-bearing module docstring.

### `echobox.py` (3 WHAT comments removed; 1 inline comment rewritten)

| Line | Removed |
|---|---|
| 732 | `# Check if resampling is needed` |
| 773 | `# Estimate duration from WAV file` |
| 789 | `# Clean up resampled file` |

Rewritten:
- Before: `# Use file modification time as a proxy for recording start`
- After: `# File mtime is the closest proxy we have to recording start for imports.`
- Before (inline): `# ffmpeg prints info to stderr`
- After (inline): `# ffmpeg prints stream info to stderr`

(Both rewrites preserve the WHY — without them a future reader would be tempted to capture stdout or assume `started_at` was authoritative.)

### `pipeline/fit.py` (4 dead-code cleanups, no comment churn)

- Line 168: dropped redundant `import platform` inside `get_hardware_info()` — already imported at module top.
- Lines 215-216: removed dead `else: pass` (no-op branch on the non-Darwin path).
- Lines 358-359: collapsed three consecutive blank lines down to two.
- Line 393 (was 399): removed trailing comma in `_build_models_endpoints(config_path, )`.

## Counts

- Files touched: 5 (`echobox.py`, `echobox_recorder/{caption_panel,menubar,recorder}.py`, `pipeline/fit.py`)
- Decorative section dividers removed: 13
- WHAT-not-WHY comments removed: 7
- WHAT-not-WHY comments rewritten as WHY: 3
- Dead code lines removed: 5
- LICENSE / attribution preambles touched: 0
- `config/echobox.yaml` / `.env` / `voices/*` touched: 0
- Public CLI / `__init__.py` docstrings touched: 0

## What was deliberately NOT removed

- `recorder.py`'s `_filter_hallucinations` per-pass algorithm descriptions (the `--- Pass N ---` decoration was the slop, not the explanation under it).
- `# noqa: ANN001` / `# pragma: no cover` / `# type: ignore[...]` linter directives.
- `enrich.py:1004`'s example-format comment (`# Match: - **[Owner Name]** task  OR  - **Owner Name** task`) — non-obvious regex, the example earns its keep.
- All security comments in `pipeline/enrich.py` (`_sanitize_prompt_field`), `pipeline/calendar.sh` (date-format injection guard), `pipeline/orchestrator.sh` (notification log permissions), `pipeline/publish.sh` (JSON-encoded password), `pipeline/speaker_id.py` (path traversal guard, pinned HF revision).
- `config/echobox.example.yaml` and `config/context-sources.example.yaml` decorative banners — those files are user-facing documentation, not source.
- All test-file comments (each one explains a fixture-setup quirk).
- All `# --- Audio capture backend ---` style headers inside YAML examples.
