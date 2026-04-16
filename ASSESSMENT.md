# DRY / Deduplication Assessment

## Investigation summary

Surveyed `pipeline/*.py`, `pipeline/*.sh`, `echobox.py`, and `echobox_recorder/*.py` for
repeated logic. Cross-referenced findings against the task's explicit guardrails (sanitizers
in `enrich.py` must stay split, recorder rate-ladder must stay defensive). Tested baseline
suite: 15/16 pass, `test_recorder` already failing due to a missing `sounddevice` module
on this dev box (unrelated to dedup work).

## Findings

### High-confidence (implemented)

1. **Shell `read_config()` + `resolve_paths()` duplicated byte-for-byte across two scripts.**
   - `pipeline/orchestrator.sh:19-56` and `pipeline/publish.sh:23-57`.
   - Same here-doc Python invocation, same `paths` env-loading loop, same fallback chain.
   - Only difference: orchestrator's `case` filter listed `AUDIO_DIR` (and silently dropped
     `STATE_DIR`); publish's filter listed `STATE_DIR` (and silently dropped `AUDIO_DIR`).
   - **Action:** Extracted both helpers to `pipeline/_shell_common.sh`. New file is
     justified because shell functions can only be reused by sourcing. The shared
     `resolve_paths` is the union (handles `AUDIO_DIR` *and* `STATE_DIR`); each caller can
     ignore vars it doesn't need. Sourcing scripts still own `ECHOBOX_DIR`, `CONFIG`,
     and `ECHOBOX_PYTHON` so the helper file has no implicit dependencies on layout.
   - Net: ~76 duplicated shell lines collapsed into a single ~52-line file plus two
     2-line `source` blocks.

2. **`run_shell_script` and `run_shell_script_capture` in `echobox.py:141-171` duplicated
   the env-construction block.**
   - Five identical lines building `env = os.environ.copy()`, setting the homebrew/local
     PATH default, and merging `extra_env`.
   - **Action:** Extracted private `_shell_script_env(extra_env)` helper at module scope,
     called from both wrappers. Eliminates drift risk on the PATH string and shrinks
     each wrapper.

### Uncertain (deferred — for human review)

The following looked superficially repeated but each call site has different semantics
or reduces complexity only marginally. Not implemented; documented here for posterity.

- **`Path(target).expanduser()` one-liners** (echobox.py, recorder.py, swift_helper.py,
  speaker_id.py, clean.py, list_calls.py, demo.py, serve.py). Each is a single-line idiom
  with no shared semantics — they're parsing different argv positions or config keys.
  Collapsing them would require an indirection that adds more lines than it saves.

- **`subprocess.run` patterns** across `status.py`, `recorder.py`, `echobox.py`,
  `fit.py`, `serve.py`, `menubar.py`, `smart_setup.py`. Each has different
  capture/timeout/check semantics:
  - `status.py` — short-timeout probes, ignore stderr
  - `fit.py` — benchmark timing, expects rc==0
  - `recorder.py` — ffmpeg invocation, fixed args
  - `menubar.py` — interactive picker via osascript
  - `serve.py` — tailscale serve management
  
  `enrich.py` already has the right consolidation point (`local_run`, `ssh_run`,
  `run_command`) for context-source invocations. Adding a generic wrapper across the
  rest would either need too many parameters or hide failure modes the call sites
  rely on.

- **`from enrich import load_config` import scattered across `pipeline/*.py`** —
  this is just an import statement, not duplicated logic. The actual `load_config` is
  defined exactly once in `enrich.py:96`; everyone else calls it. Working as intended.

- **`fit.py:read_config_value` vs `enrich.py:get_config`** — these look similar by name
  but `fit.py` does line-level YAML editing (so `fit` can write the recommended models
  back), while `enrich.py:get_config` reads from the flattened in-memory dict. Different
  read/write contracts; don't merge.

- **Two sanitizers in `enrich.py`** (`_sanitize_prompt_field`, `_sanitize_context_term`) —
  task explicitly forbade merging. Skipped.

- **`is_writable` in `pipeline/status.py:68` vs `can_write_directory` in
  `echobox.py:174`** — same concept (probe-write a file, swallow exceptions) but they
  live in different modules and `status.py` runs as a freestanding script with its own
  helper conventions. A two-call-site dedup with cross-file import doesn't clear the
  three-site bar; left alone.

- **`STATE_DIR` fallback ordering** — orchestrator's old `resolve_paths` set
  `STATE_DIR="${STATE_DIR:-$(dirname "$REPORT_DIR")}"` *without* picking up
  `STATE_DIR` from `read_config.py paths`. The shared helper now reads `STATE_DIR`
  from the paths output and falls back the same way. This is a tiny behavior fix —
  the env override path was already broken in orchestrator. Worth noting in case it
  ever surfaces a regression.

## Test result

`./echobox test` → **15 pass, 1 fail** (same as baseline).

Failing test: `test_recorder`, due to `ModuleNotFoundError: No module named 'sounddevice'`
on this worktree — pre-existing environment issue, unrelated to the dedup work.

## Files changed

- `pipeline/_shell_common.sh` — new (52 lines)
- `pipeline/orchestrator.sh` — replaced 41 lines of duplicated helpers with 4 sourcing lines
- `pipeline/publish.sh` — replaced 36 lines of duplicated helpers with 4 sourcing lines
- `echobox.py` — extracted `_shell_script_env` helper, simplified two wrappers
- `ASSESSMENT.md` — new (this file)
