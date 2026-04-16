# AGENTS.md

> This file provides project instructions for AI coding assistants (Gemini CLI, Cursor, Codex, Windsurf, and others). See also: `CLAUDE.md` for the same project guidance in Claude-oriented format.

Echobox records calls, transcribes them locally, diarizes speakers, identifies enrolled voices, enriches the transcript with a local MLX server plus optional project context, and publishes an HTML report. The macOS path is end-to-end: `echobox watch` uses the built-in recorder with dual-stream capture (BlackHole for the remote side + local mic for the user's voice), mixes both tracks via ffmpeg `amix`, transcribes locally, and triggers the pipeline automatically.

**CRITICAL: BlackHole + Multi-Output Device is REQUIRED for the remote side.** The recorder opens two parallel sounddevice InputStreams — one on BlackHole 2ch (captures other participants via the system audio loopback) and one on the user's local mic (AirPods / USB / built-in, auto-selected from the macOS default input device). Multi-Output Device (AirPods + BlackHole 2ch) must be configured in Audio MIDI Setup so the user hears audio AND BlackHole captures it. NEVER suggest removing BlackHole.

## 30-Second Mental Model

- `./install.sh` checks dependencies, creates `config/echobox.yaml`, runs model fit unless you keep existing model settings, creates `~/echobox-data/*`, and writes an optional launchd service file.
- `./echobox status` is the fastest way to see what is missing.
- `./echobox fit` writes recommended `whisper_model` and `mlx_model` values into `config/echobox.yaml`.
- `./echobox demo` exercises the enrichment flow without requiring a running LLM server.
- `./echobox watch` is the real automatic pipeline entrypoint on macOS only.
- **Dual-stream capture**: recorder writes `<slug>.wav` (mixed), `<slug>-remote.wav` (BlackHole), and `<slug>-local.wav` (AirPods/mic) to `~/echobox-data/audio/`. If the local mic stream can't open (PortAudio `-9986`, AirPods SCO link-mode race), the recorder walks a rate ladder `[reported, 48000, 16000, 44100]` and falls back to the MacBook Pro built-in mic at 48000Hz before giving up.
- **Voice ID**: `pipeline/speaker_id.py` extracts wespeaker-voxceleb-resnet34-LM embeddings per diarized segment and replaces `SPEAKER_XX` with enrolled display names when cosine similarity ≥ 0.55. Enroll via `./echobox enroll-voice <slug> <wav> <display name>`. Enrolled embeddings live in `voices/<slug>.{npy,json}` (gitignored — biometric data).
- **Pipeline steps**: `[1/5]` LLM enrichment → `[1.5/5]` slug derivation from enrichment output → `[2/5]` optional workstation sync → `[3/5]` HTML publish → `[4/5]` optional meeting-notes rsync → `[5/5]` notification. Notification attempts are audited to `~/echobox-data/logs/notifications.log` with full stdout/stderr/exit code.
- `./echobox serve` runs a password-gated HTTP server for locally published reports.
- Publishing has two separate knobs:
  - `publish.engine`: `local` or `claude` for HTML generation.
  - `publish.platform`: `local` or `vercel` for where the report is published.
  - `echobox serve --tunnel tailscale|bore` is separate from publish and shares local reports over HTTP.

## Data Directory Layout

```
~/echobox-data/
  transcripts/   # .txt transcripts (one per call)
  audio/         # <slug>.wav, <slug>-local.wav, <slug>-remote.wav
  enrichments/   # <slug>-enriched.md + sidecar .json
  reports/       # <slug>-enriched/report.html
  logs/          # watcher.log, pipeline.log, notifications.log
  sessions/      # swift_helper backend: per-session dir (opt-in)
```

Audio is split from transcripts so retention can target the large files without touching the text outputs. Legacy installs (pre-audio-dir) have `.wav` files still inside `transcripts/`; the cleanup sweep walks both locations so nothing is missed.

## Updating Recorder Code

**If you edit `echobox_recorder/recorder.py`, you MUST kickstart the launchd watcher afterwards** — Python modules are loaded once at process start. Editing the file on disk has zero effect on the running watcher until restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.echobox.watcher
```

This is the #1 source of "I changed the code but nothing happened" confusion. The orchestrator shell (`pipeline/orchestrator.sh`) is re-read per pipeline run, so shell-only edits apply immediately; Python-level edits do not.

## Microphone TCC on First Run

macOS Microphone permission (TCC) must be granted to the Python process before the local mic stream can open. launchd-managed processes CANNOT trigger the permission dialog themselves, so the first run must be from **Terminal.app** directly (not over SSH, not via launchd). Run `./echobox watch` manually once, click **Allow** on the TCC prompt, then load the launchd plist.

## Actual Setup Path

If a user says "set up Echobox on my machine", follow this order:

1. Run `./install.sh`.
2. Install **BlackHole 2ch** via Homebrew and create a **Multi-Output Device** in Audio MIDI Setup (AirPods/headphones + BlackHole 2ch), set it as the system Output.
3. Create `~/echobox/.env` with `HF_TOKEN=hf_...` (Read scope). **Accept the licenses** for `pyannote/speaker-diarization-3.1` and `pyannote/wespeaker-voxceleb-resnet34-LM` on huggingface.co while logged in with the same token.
4. Run `./echobox status`.
5. Edit `config/echobox.yaml` directly if needed (including the `audio_dir` and `cleanup:` sections if you want custom retention policy).
6. Re-run `./echobox fit` only if you want to change or re-benchmark model choices.
7. Start the local LLM server that matches `mlx_url`.
8. Run `./echobox demo`.
9. **First run `./echobox watch` from Terminal.app directly** (not SSH, not launchd yet) so macOS can show the Microphone TCC prompt. Click **Allow**.
10. Load the launchd plist so the watcher auto-starts on login.
11. Optionally enroll voices: `./echobox enroll-voice marc ref.wav "Marc Zeller"`.

Important:

- `./install.sh` already creates `config/echobox.yaml`. If you run `./echobox setup` after `./install.sh`, the wizard exits early because the config already exists.
- Use `./echobox setup` only when the user wants the minimal interactive wizard and either has no config yet or is willing to delete `config/echobox.yaml` first.
- Recording is built in; no external recorder patching is required.
- **After any edit to `echobox_recorder/*.py`, kickstart the watcher** (see "Updating Recorder Code" above).

## Intelligent Setup (Agent Playbook)

For alpha users, treat intelligent setup as an agent-assisted playbook, not as silent product automation. The goal is to draft a good `config/echobox.yaml` faster, with explicit user consent around private data.

Recommended sequence:

1. Run `./install.sh` and `./echobox status` first.
2. Treat the model-fit step inside `./install.sh` as part of the default setup flow; only re-run it manually if you want different recommendations.
3. Ask for consent before probing private sources like Calendar, Messages, Slack exports, or local notes.
4. Run `./echobox smart-setup` to probe the machine and draft context-source recommendations.
5. Interview the user about actual meeting types: investor, board, client, team sync, recruiting, support, and 1:1s.
6. If the user consents and a calendar CLI is available, run `./echobox smart-setup --with-calendar` to inspect recent event titles and attendee domains.
7. Edit `config/echobox.yaml` directly. Do not let the probe script silently rewrite it.
8. Review the suggested `context_sources.*`, `team.*`, and `meeting_types.*` values with the user before enabling them.

What to probe on macOS:

- Calendar CLIs: `gws`, `gcalcli`, `icalBuddy`
- Messaging sources: `~/Library/Messages/chat.db`, Slack.app presence, Slack export folders if the user points to them
- Project context: `PROJECT_DIR`, common project folders, notes directories, Obsidian presence
- Recording prerequisites: `ffmpeg`, `sounddevice`

Guardrails:

- Prefer metadata probes over reading private content. Presence, readability, and command availability are usually enough for the first pass.
- Calendar inspection is opt-in. Use it to infer common title patterns, not to dump event contents into docs or chat.
- Treat Messages.app access as privacy-sensitive and macOS-permission-sensitive.
- Never commit user-specific paths, tokens, passwords, or generated config values.

## Command Surface

These are the commands exposed by `./echobox` today:

| Command | Purpose |
|---------|---------|
| `echobox list` | Show recent calls and whether each has a transcript, enrichment, and report |
| `echobox search <term>` | Search across transcripts and enrichments |
| `echobox open [report]` | Open the latest report or a named report |
| `echobox preview [call-or-file]` | Render an enrichment markdown file in the terminal |
| `echobox actions` | Aggregate action items across all enriched calls |
| `echobox summary` | Weekly summary across recent calls |
| `echobox reprocess <name>` | Re-enrich and re-publish an existing call |
| `echobox enrich <transcript>` | Run LLM enrichment on a transcript file |
| `echobox publish <enrichment>` | Generate and optionally deploy an HTML report |
| `echobox serve [--port N] [--tunnel tailscale|bore]` | Serve local reports with a password gate |
| `echobox watch` | Start the built-in macOS watcher and recorder |
| `echobox enroll-voice <slug> <wav> <name>` | Enroll a reference voice for speaker identification |
| `echobox voices [list\|delete <slug>]` | Manage enrolled voices (default action is list) |
| `echobox setup` | Minimal interactive config wizard |
| `echobox smart-setup [--with-calendar]` | Probe the machine and draft setup recommendations |
| `echobox status` | Check dependencies, config, model server reachability, and data dirs |
| `echobox fit` | Benchmark and select Whisper and LLM models |
| `echobox config` | Show parsed config values |
| `echobox clean [--older N] [--prune] [--audio]` | Show disk usage, optionally prune (include `.wav` with `--audio`) |
| `echobox quality` | Run pipeline and context quality checks |
| `echobox demo` | Walk through the pipeline using fixtures |
| `echobox test` | Run Python smoke tests |
| `echobox version` | Print version |
| `echobox help` | Show CLI help |

Commands that were easy to miss in the old docs: `preview`, `actions`, `summary`, `quality`, `test`, `serve`, `enroll-voice`, and `voices`.

## Configuration

The main config file is `config/echobox.yaml`. `./install.sh` creates it from `config/echobox.example.yaml` if it does not exist.

Key settings:

- `whisper_model`: transcription model (`mlx-whisper` Hugging Face repo path).
- `mlx_model`: enrichment model name to use with the configured server.
- `mlx_url`: OpenAI-compatible chat completions endpoint.
- `workstation_ssh`: optional two-machine enrichment target.
- `context_sources.*`: calendar, messages, documents, and web inputs.
- `meeting_types.*`: call classification rules that choose which context sources to use.
- `publish.engine`: `local` or `claude` HTML generation.
- `publish.platform`: `local` or `vercel` report publishing.
- `publish.password`: shared password for `echobox serve` and the Vercel gate.
- `publish.scope`: Vercel gate setting.
- `notify.enabled` and `notify.command`: post-publish notification hook.
- `transcript_dir`, `enrichment_dir`, `report_dir`, and `log_dir`: overridable storage locations.

## Share Reports

Use `./echobox serve` when reports are already published locally and you want a password-gated HTTP server.

- `./echobox serve`: bind locally on port `8090`
- `./echobox serve --port 9000`: use a different local port
- `./echobox serve --tunnel bore`: expose the local server through Bore
- `./echobox serve --tunnel tailscale`: expose the local server through `tailscale serve`

Sharing tiers:

- `local`: local port only, suitable for LAN access or your own tunnel
- `tailscale`: local port plus `tailscale serve` for team access over Tailscale
- `vercel`: existing deploy flow via `./echobox publish`, unchanged

Guardrails:

- `publish.password` must be set to a real value before serving.
- `echobox serve` only serves files from `report_dir`; it does not publish new reports.
- Treat Bore and Tailscale URLs as authenticated-but-public endpoints for anyone with the password.

## Configure Context Sources (critical for enrichment quality)

Enrichment quality depends entirely on what context the LLM receives alongside the transcript. Your job as an agent is to discover what's available on this specific machine and configure the best sources. **Do not skip this step — without context, the LLM only has raw transcript and produces generic output.**

### Step 1: Probe the machine

Run these checks and note what's available:

```bash
# Calendar — check in order of preference
which gws && echo "gws: YES"                                    # Google Workspace CLI
which gcalcli && echo "gcalcli: YES"                             # gcalcli
ls ~/Library/Calendars/ 2>/dev/null && echo "Apple Calendar: YES" # native Calendar.app
osascript -e 'tell application "Calendar" to get name of calendars' 2>/dev/null  # verify access

# Documents — Spotlight indexes everything on macOS
mdfind -count "kind:document" && echo "mdfind: YES"              # always available on macOS

# Messages
ls ~/Library/Messages/chat.db 2>/dev/null && echo "Messages.app SQLite: YES"
ls ~/Library/Group\ Containers/*/store.db 2>/dev/null && echo "Slack local DB: YES"

# Contacts
osascript -e 'tell application "Contacts" to get name of every person' 2>/dev/null | head -1 && echo "Contacts.app: YES"

# Notes
osascript -e 'tell application "Notes" to get name of every note' 2>/dev/null | head -1 && echo "Notes.app: YES"
```

### Step 2: Configure calendar source

Pick the best available option and write it to `config/echobox.yaml`:

| Available | Calendar command to configure |
|-----------|------------------------------|
| `gws` | `gws calendar events list --params '{"calendarId":"primary","timeMin":"{date}T00:00:00Z","timeMax":"{date}T23:59:59Z","singleEvents":true}'` |
| `gcalcli` | `gcalcli agenda "{date} 00:00" "{date} 23:59" --details all --tsv` |
| Apple Calendar only | `osascript -e 'tell application "Calendar" to set evts to (every event of every calendar whose start date > date "{date}") \n repeat with e in evts \n log (summary of e) & "|" & (start date of e) \n end repeat'` |
| None | Leave disabled — enrichment will work without calendar, just no attendee matching |

### Step 3: Configure document search

`mdfind` is available on every Mac. Configure it as the default:

```yaml
context_sources:
  documents:
    enabled: true
    command: "mdfind '{term}' | head -5 | xargs -I{} head -20 '{}' 2>/dev/null"
```

This searches Notes, PDFs, Word docs, plain text — anything Spotlight indexes. No setup required.

### Step 4: Configure message context (optional)

Only if the user has relevant message databases:

| Available | Configuration |
|-----------|---------------|
| Messages.app | `type: sqlite`, `path: ~/Library/Messages/chat.db`, `query: SELECT text FROM message WHERE text LIKE '%{term}%' ORDER BY date DESC LIMIT 10` |
| Custom CLI | `type: command`, `command: "your-search-tool '{term}'"` |
| Nothing | Leave disabled |

**Important:** Messages.app SQLite access may require Full Disk Access permission for the terminal app.

### Step 5: Ask the user what they need

After probing, tell the user what you found and ask:
- "I found Calendar.app and Spotlight on your machine. Want me to enable calendar context and document search?"
- "I see Messages.app — want me to include recent messages with call attendees as context? (Requires Full Disk Access)"
- "What kind of calls do you take? (investor, team standup, client) — this helps me set up meeting type patterns"

### Step 6: Verify context works

After configuring, run `./echobox enrich tests/fixtures/2026-03-15_10-00_roadmap-sync.txt --verbose` and check the context stats line:
- `Context: 0 chars, 0 sections` = broken, nothing was injected
- `Context: 500 chars, 2 sections` = working, calendar + docs flowing

If zero context, check the calendar command manually: `bash -c "<calendar_command>"` with today's date.

## Change The Model

If a user says "change the model", do this exactly:

1. Edit `mlx_model` in `config/echobox.yaml`.
2. If they are also changing backend details, keep `mlx_url` on the MLX server endpoint:
   - MLX server: `http://localhost:8090/v1/chat/completions`
3. Restart or reconfigure the serving process so that model is actually loaded.
4. Run `./echobox status` to verify the endpoint is reachable.

If they mean transcription quality or speed rather than enrichment quality, edit `whisper_model` instead, or run `./echobox fit` to rewrite both recommended model fields.

## Diagnose A Broken Pipeline

If a user says "my pipeline isn't working", start with this sequence:

1. Run `./echobox status`.
2. Run `./echobox config` to confirm parsed values.
3. Run `./echobox demo` to separate prompt and report logic from live recording issues.
4. Check logs in `~/echobox-data/logs/`:
   - `watcher.log` — recording lifecycle, dual-stream status, speaker ID results
   - `pipeline.log` — [1/5]–[5/5] steps
   - `notifications.log` — every [5/5] notify attempt with full stdout/stderr/exit code
   - `echobox.log` / `echobox.err` — stdio from the launchd watcher
5. Inspect `~/echobox-data/logs/watcher.log` for browser detection or recorder errors.

Fast symptom mapping:

- **Only one side of conversation in transcript** / "VAD: no speech detected": BlackHole not receiving signal. Fix: Audio MIDI Setup → Multi-Output Device (AirPods + BlackHole 2ch) → set as system Output. The menu bar now surfaces a ⚠ **Audio routing** warning when this is broken; clicking it opens Audio MIDI Setup.
- **My own voice is missing from the transcript**: local mic stream failed to open. Look for `Local stream open failed ... PaErrorCode -9986` in `watcher.log`. The recorder walks a rate ladder and falls back to MacBook Pro Mic — if ALL fall back too, the ladder is exhausted. Common cause: AirPods in a transient SCO link-mode. Disconnect + reconnect AirPods (or just wait for a few seconds and let them settle) then kickstart the watcher.
- **Voice ID didn't label me / "SPEAKER_XX" instead of Marc**: check `watcher.log` for `Speaker SPEAKER_00 -> <name> (cosine=...)`. If cosine is < 0.55, the enrolled reference isn't close enough to today's speech — re-enroll from a longer, more natural clip via `./echobox enroll-voice`.
- **Transcript written but report never arrives on Telegram / Slack**: check `~/echobox-data/logs/notifications.log` for the exact exit code and command output. The orchestrator captures stdout+stderr, so the failure mode (HTML parse error, rate limit, SSH hang) is visible there.
- **I edited `echobox_recorder/recorder.py` and nothing changed**: the running watcher has the OLD code in memory. Run `launchctl kickstart -k gui/$(id -u)/com.echobox.watcher` and re-test.
- **Transcript full of repeated garbage** ("Takk for", "ご視聴ありがとうございました"): Whisper hallucinations on silence. The hallucination filter should catch this. If not, check `_filter_hallucinations()` in recorder.py. Setting `whisper_language` in config can also help.
- **Enrichment in wrong language**: The pipeline auto-detects language from transcript content. If detection is wrong, check `detect_language()` in enrich.py or set `whisper_language` in config.
- Call detection never starts on macOS: inspect the built-in watcher logs and browser/tab detection.
- Transcript exists but enrichment fails: check `mlx_url`, model server status, and `HF_TOKEN`.
- Enrichment works but report generation or deploy fails: check `publish.engine`, `publish.platform`, Claude CLI, and Vercel CLI separately.

## Repo Structure

```text
echobox/
  echobox                       Main CLI entrypoint
  install.sh                       Installer and bootstrapper
  README.md                        Human-facing overview
  AGENTS.md / CLAUDE.md            Agent-facing setup and operational guidance

  config/
    echobox.example.yaml           Main config template
    context-sources.example.yaml   Context source examples

  pipeline/
    orchestrator.sh                Transcript -> enrich -> publish -> notify pipeline
    calendar.sh                    Calendar lookup helper
    enrich.py                      Transcript/context -> LLM enrichment
    fit.py                         Hardware-aware model selection
    publish.sh                     HTML generation and optional Vercel publish
    read_config.py                 Config reader used by shell scripts
    clean.py                       Retention / prune logic, incl. prune_audio()
    speaker_id.py                  Voice enrollment + wespeaker identification
    slug_from_enrichment.py        Slug derivation from LLM enrichment output
    markdown_preview.py            Terminal markdown preview fallback

  quality/
    pipeline-check.sh              Pipeline health checks
    context-check.sh               Context-enrichment checks
    repo-quality.sh                Repo hygiene checks
  templates/
    report.html                    Local HTML template
    enrichment_prompt.txt          Optional prompt template override
    gate.js                        Vercel password gate

  tests/
    fixtures/                      Demo sample inputs
    test_*.py                      Smoke tests (incl. test_swift_helper.py)

  echobox_recorder/
    __init__.py                    Public recorder API and attribution
    watcher.py                     Built-in meeting detection for macOS
    recorder.py                    Dual-stream audio capture + local transcription
    menubar.py                     rumps menu bar app (disk / routing / voices)
    caption_panel.py               Swift helper live caption panel (NSPanel)
    swift_helper.py                Swift capture helper backend (opt-in)
    LICENSE                        Upstream MIT attribution for vendored code

  swift/echobox-capture/           Opt-in Swift capture binary (WhisperKit / process-tap)
  system-audio-tap/                Core Audio process-tap helper (macOS 14.2+)

  voices/                          Enrolled speaker embeddings (GITIGNORED, biometric)
  scripts/
    run-echobox.sh                 Wrapper that sources .env before running echobox

  .env.example                     HF_TOKEN template — copy to .env (gitignored)

  docs/
    setup.md                       Detailed platform-specific setup
    context-sources.md             Context source configuration
    design-decisions.md            Architecture rationale
    troubleshooting.md             Troubleshooting reference
```

## Guardrails

- Preserve the attribution notice in `echobox_recorder/LICENSE` when modifying the vendored recorder code.
- `templates/report.html` uses CSS variables for theming; do not hardcode colors into the template.
- `config/echobox.yaml` may contain real passwords, tokens, and local paths; **never commit it** — it is gitignored.
- **Never commit** `.env`, `voices/*.npy`, `voices/*.json`, `*.key`, `*.crt`, `*.ts.net.*`, `*.bak*`, or anything under `swift/**/.build/` or `system-audio-tap/.build/`. These are all covered by `.gitignore`; do not override them.
- Enrolled voice embeddings are biometric data — treat them as PII. They live in `voices/` and must stay local.
- When editing `echobox_recorder/*.py`, always kickstart the launchd watcher afterwards (see "Updating Recorder Code"). Editing the file without restarting leaves the old code running and creates "I changed it but nothing happened" confusion.
- When staging files for commit, prefer explicit `git add <path>` over `git add -A` / `git add .` — sweeping everything risks staging `.env`, local `.wav` files, or Swift build artifacts if they ever slip past gitignore.

## See Also

`CLAUDE.md` contains the same project context formatted for Claude Code.
