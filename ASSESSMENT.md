# Legacy Code Assessment

Scope: identify dead/legacy/duplicate paths in the Echobox repo as of `worktree-agent-a132ccbc`.
Verdict: **codebase is fairly clean**. One dead script, one stale doc reference, plus a handful
of intentional-but-easy-to-misread patterns documented below. Total removals are small.

---

## High-confidence removals (acted on)

### 1. `pipeline/ingest.sh`
- **Status:** Removed.
- **Evidence:** `grep -rn "ingest.sh" .` returns zero hits. `grep -rn "ECHOBOX_LAPTOP" .` only
  finds the variable inside `ingest.sh` itself plus `quality/pipeline-check.sh`'s
  `check_remote()` helper, which uses `ECHOBOX_LAPTOP` independently and works without
  `ingest.sh` being present.
- **Why dead:** The two-machine sync flow is wired through `workstation_ssh` in
  `pipeline/orchestrator.sh` (push from laptop → workstation), not via a manual
  workstation-side pull. Nothing — no script, no docs, no test — invokes `ingest.sh`.
- **Risk:** None. Removing the file does not affect `pipeline-check.sh`,
  `orchestrator.sh`, or any tested workflow.

### 2. `*.diff` reference in `CLAUDE.md` / `AGENTS.md`
- **Status:** Removed lines from both files.
- **Evidence:** The Repo Structure tree lists `echobox_recorder/*.diff   Human-readable
  patch descriptions`. `find . -name "*.diff"` returns zero files. The vendored recorder
  no longer carries upstream patch diffs (design-decisions.md confirms the recorder is
  vendored, not patched on top of an external install).
- **Risk:** None. This is purely doc cleanup.

---

## Considered but kept (NOT legacy)

### `pipeline/clean.py::prune_audio` walking both `audio_dir` and `transcript_dir`
Explicitly called out in CLAUDE.md as intentional pre-audio-dir-split install
compatibility. Left untouched.

### `echobox_recorder/recorder.py` rate ladder `[reported, 48000, 16000, 44100]`
Explicitly called out in CLAUDE.md as AirPods SCO race mitigation. Left untouched.

### Multiple calendar CLI parsers in `pipeline/enrich.py` / `pipeline/calendar.sh`
TSV / JSON / osascript paths exist on purpose to match what `gcalcli`, `gws`, and Apple
Calendar each emit. Documented in CLAUDE.md "Configure Context Sources". Not legacy.

### Swift helper backend (`echobox_recorder/swift_helper.py`, `swift/echobox-capture/`,
`system-audio-tap/`)
Opt-in `capture.backend: swift_helper` path. `tests/test_swift_helper.py` exercises it,
config example documents it. Not legacy.

### Whisper hallucination filter in `recorder.py::_filter_hallucinations`
Current defense against Whisper-on-silence garbage. CLAUDE.md "Diagnose A Broken
Pipeline" lists it as the active mitigation.

### `prepare_transcript_for_prompt` `[Unknown]` handling in `pipeline/enrich.py`
Current undiarized-fallback path; covered by tests.

### Comment "legacy slug" in `pipeline/slug_from_enrichment.py`
This is English wording inside a docstring describing the original timestamp slug. It is
not a label on legacy code. Left untouched.

### Comment "orchestrator/legacy code" in `recorder.py::_start_swift`
Just describes that the Swift session WAV is surfaced via convention so the orchestrator
(which is not legacy) can find it. Wording could be tightened but functionality is
current. Left untouched.

### `transcribe` subcommand in `echobox.py`
Real, functional command (`./echobox transcribe <wav>`). It is missing from the
"Command Surface" table in CLAUDE.md / AGENTS.md, but the command itself is current
code — not legacy. Documenting it is out of scope for this cleanup pass.

### `meeting_notes.*` config keys
Read by `pipeline/orchestrator.sh` (`meeting_notes.ssh_host`, `meeting_notes.base_dir`,
`meeting_notes.reports_dir`) and mentioned at a high level in `README.md` step 14, but
not present in `config/echobox.example.yaml`. This is a documentation gap, not dead
code. Left for a separate docs pass.

### `ECHOBOX_LAPTOP` in `quality/pipeline-check.sh`
Powers the optional `check_remote()` helper that lets `pipeline-check.sh` run its
greps over SSH against a recording machine. Independent of the removed `ingest.sh`.
Left untouched.

### `cmd_serve --tunnel` paths (tailscale / bore)
Both are documented and supported sharing tiers (`./echobox serve --tunnel
tailscale|bore`). Not legacy.

### Vercel publish path in `pipeline/publish.sh`
Documented `publish.platform: vercel`. Not legacy.

---

## Uncertain / left as-is

None worth flagging — the codebase has no stale feature flags, no version-suffixed
modules (`_v2`, `_old`, `_new`), no commented-out blocks, and no `if False:` guards.
The only `# disabled` comments in `menubar.py` describe transient menu-item state, not
disabled features.
