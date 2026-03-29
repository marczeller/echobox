# CLAUDE.md

> This file provides project instructions for Claude Code. See also: `AGENTS.md` for the same project guidance in tool-agnostic format.

Echobox records calls, transcribes them locally, diarizes speakers, enriches the transcript with a local MLX server plus optional project context, and publishes an HTML report. The macOS path is end-to-end: `echobox watch` uses the built-in recorder, captures system audio through BlackHole, transcribes locally, and triggers the pipeline automatically.

## 30-Second Mental Model

- `./install.sh` checks dependencies, creates `config/echobox.yaml`, runs model fit unless you keep existing model settings, creates `~/echobox-data/*`, and writes an optional launchd service file.
- `./echobox status` is the fastest way to see what is missing.
- `./echobox fit` writes recommended `whisper_model` and `mlx_model` values into `config/echobox.yaml`.
- `./echobox demo` exercises the enrichment flow without requiring a running LLM server.
- `./echobox watch` is the real automatic pipeline entrypoint on macOS only.
- Publishing has two separate knobs:
  - `publish.engine`: `local` or `claude` for HTML generation.
  - `publish.platform`: `local` or `vercel` for where the report is published.

## Actual Setup Path

If a user says "set up Echobox on my machine", follow this order:

1. Run `./install.sh`.
2. Run `./echobox status`.
3. Edit `config/echobox.yaml` directly if needed.
4. Re-run `./echobox fit` only if you want to change or re-benchmark model choices.
5. Start the local LLM server that matches `mlx_url`.
6. Run `./echobox demo`.
7. Run `./echobox watch`.

Important:

- `./install.sh` already creates `config/echobox.yaml`. If you run `./echobox setup` after `./install.sh`, the wizard exits early because the config already exists.
- Use `./echobox setup` only when the user wants the minimal interactive wizard and either has no config yet or is willing to delete `config/echobox.yaml` first.
- Recording is built in; no external recorder patching is required.

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

- Calendar CLIs: `gcalcli`, `gws`, `icalBuddy`
- Messaging sources: `~/Library/Messages/chat.db`, Slack.app presence, Slack export folders if the user points to them
- Project context: `PROJECT_DIR`, common project folders, notes directories, Obsidian presence
- Recording prerequisites: `ffmpeg`, `sounddevice`, BlackHole

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
| `echobox watch` | Start the built-in macOS watcher and recorder |
| `echobox setup` | Minimal interactive config wizard |
| `echobox smart-setup [--with-calendar]` | Probe the machine and draft setup recommendations |
| `echobox status` | Check dependencies, config, model server reachability, and data dirs |
| `echobox fit` | Benchmark and select Whisper and LLM models |
| `echobox config` | Show parsed config values |
| `echobox clean [--older N]` | Show disk usage, provide prune commands |
| `echobox quality` | Run pipeline and context quality checks |
| `echobox demo` | Walk through the pipeline using fixtures |
| `echobox test` | Run Python smoke tests |
| `echobox version` | Print version |
| `echobox help` | Show CLI help |

Commands that were easy to miss in the old docs: `preview`, `actions`, `summary`, `quality`, and `test`.

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
- `publish.password` and `publish.scope`: Vercel gate settings.
- `notify.enabled` and `notify.command`: post-publish notification hook.
- `transcript_dir`, `enrichment_dir`, `report_dir`, and `log_dir`: overridable storage locations.

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

After configuring, run `./echobox demo --verbose` and check the context stats line:
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
   - `watcher.log`
   - `pipeline.log`
   - `echobox.log`
   - `echobox.err`
5. Inspect `~/echobox-data/logs/watcher.log` for browser detection or recorder errors.

Fast symptom mapping:

- Call detection never starts on macOS: inspect the built-in watcher logs, BlackHole routing, and browser/tab detection.
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
    orchestrator.sh                Transcript -> enrich -> publish -> notify
    ingest.sh                      Move raw artifacts into data dirs
    calendar.sh                    Calendar lookup helper
    enrich.py                      Transcript/context -> LLM enrichment
    fit.py                         Hardware-aware model selection
    publish.sh                     HTML generation and optional Vercel publish
    read_config.py                 Config reader used by shell scripts
    markdown_preview.py            Terminal markdown preview fallback

  quality/
    pipeline-check.sh              Pipeline health checks
    context-check.sh               Context-enrichment checks
    repo-quality.sh                Repo hygiene checks
    composite-score.sh             Composite scoring helper

  templates/
    report.html                    Local HTML template
    enrichment_prompt.txt          Optional prompt template override
    gate.js                        Vercel password gate

  tests/
    fixtures/                      Demo sample inputs
    test_*.py                      Smoke tests

  echobox_recorder/
    __init__.py                    Public recorder API and attribution
    watcher.py                     Built-in meeting detection for macOS
    recorder.py                    Audio capture and local transcription
    LICENSE                        Upstream MIT attribution for vendored code
    *.diff                         Human-readable patch descriptions

  docs/
    setup.md                       Detailed platform-specific setup
    context-sources.md             Context source configuration
    design-decisions.md            Architecture rationale
    troubleshooting.md             Troubleshooting reference
```

## Guardrails

- Preserve the attribution notice in `echobox_recorder/LICENSE` when modifying the vendored recorder code.
- `templates/report.html` uses CSS variables for theming; do not hardcode colors into the template.
- `config/echobox.yaml` may contain real passwords, tokens, and local paths; never commit user-specific values.

## See Also

`AGENTS.md` contains the same project context formatted for non-Claude AI agents.
