# Unused Code Assessment

## Investigation approach

Cross-reference analysis between the dispatcher (`echobox.py` + `pipeline/orchestrator.sh` + `pipeline/publish.sh`), every Python module under `pipeline/` and `echobox_recorder/`, and every `tests/test_*.py`.

Tools used:
- `Grep` (ripgrep): static reference search across the whole tree
- `vulture` (installed into the existing `.venv`) at `--min-confidence 60/70/80/100`
- `Read`: targeted inspection of suspect call sites
- `./echobox test` baseline + post-change verification

## Findings

Conventions: `name @ file:line  refs=N  decision`

### High-confidence removals (executed)

| Symbol | Location | Refs | Decision |
|---|---|---|---|
| `pipeline/ingest.sh` (whole file) | `pipeline/ingest.sh` | 0 (not even in CLAUDE.md repo-structure listing) | REMOVE |
| `command_output()` | `pipeline/status.py:19` | 0 | REMOVE |
| `relabel_transcript()` | `pipeline/speaker_id.py:237` | 0 — `recorder.py` only consumes `identify_speakers`; `speaker_id.py main()` never dispatches to it | REMOVE |
| `MAX_CHARS_PER_LINE` | `echobox_recorder/caption_panel.py:35` | 0 (only `MAX_LINES` is read) | REMOVE |
| `helper_self_test()` | `echobox_recorder/swift_helper.py:363` | 0 (docstring claims "Used by `echobox status` and CI" but no caller exists in `pipeline/status.py` or anywhere else) | REMOVE |
| bare `import swift_helper` | `tests/test_swift_helper.py:24` | 0 (the named imports on the next line are what the test uses) | REMOVE |

### Uncertain (left in place, documented)

| Symbol | Location | Reason kept |
|---|---|---|
| `pipeline/calendar.sh` | `pipeline/calendar.sh` | No code refs. BUT it is documented in CLAUDE.md/AGENTS.md as the "Calendar lookup helper" and exposes a stable `ECHOBOX_CALENDAR_CMD` interface; could be invoked externally by a user shell wrapper. Calendar fetching inside `enrich.py` uses `context_sources.calendar.command` directly and bypasses this script today. Treat as user-facing utility. |
| `Foundation` import | `echobox_recorder/caption_panel.py:26` | pyobjc import side effects — removing it may break NSPanel runtime even if Vulture sees no reference. AppKit cohort, leave intact. |
| `time_info` callback args | `echobox_recorder/recorder.py:374,383` | Required by sounddevice / PortAudio callback signature. |
| `frame`, `signum` | `echobox_recorder/menubar.py:149` | Required by Python `signal.signal()` handler signature. |
| `_tick`, `do_GET`, `do_POST`, `log_message` | menubar.py / pipeline/serve.py | Framework callbacks (rumps `@rumps.timer(3)`, `BaseHTTPRequestHandler`); invoked dynamically. |
| `last_level_rms`, `stop_requested` and similar attributes | `echobox_recorder/swift_helper.py` | Read by callers from outside the class via attribute access; vulture cannot track cross-module attribute reads. |
| Vendored upstream code paths in `echobox_recorder/` | various | Per CLAUDE.md guardrail: preserve upstream branches even when locally unused. |
| `prune_audio()` legacy dual-directory sweep | `pipeline/clean.py` | Intentional legacy-install compatibility per CLAUDE.md. |
| Multiple calendar CLI fallbacks (gws/gcalcli/osascript) | `pipeline/enrich.py` and config templates | Intentional user-support fallbacks per CLAUDE.md. |
| Vendored `echobox_recorder/LICENSE` | recorder package | Attribution; never touch per guardrail. |

### Notes

- `echobox_recorder/*.diff` files mentioned in CLAUDE.md repo-structure no longer exist on disk. Not a code issue (docs lag), no removal needed.
- All test fixtures under `tests/fixtures/` are referenced by at least one test (verified via grep on basename).
- No high-confidence findings in `pipeline/report_render.py`, `pipeline/demo.py`, `pipeline/enrich.py`, `pipeline/list_calls.py`, `pipeline/summary.py`, `pipeline/actions.py`, `pipeline/search.py`, `pipeline/serve.py` (all defs reachable from CLI handlers, shell scripts, or tests).

## Verification

`./echobox test` — 16/16 tests pass before and after the removals.
