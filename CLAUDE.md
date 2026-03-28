# CLAUDE.md

> This file provides project instructions for Claude Code. See also: `AGENTS.md` for the same project guidance in tool-agnostic format.

Echobox records calls, transcribes them locally, diarizes speakers, enriches the transcript with a local MLX server plus optional project context, and publishes an HTML report. The macOS path is end-to-end: `trnscrb` detects calls, records through BlackHole, and triggers the pipeline automatically.

## 30-Second Mental Model

- `./install.sh` checks dependencies, creates `config/echobox.yaml`, creates `~/echobox-data/*`, and writes an optional launchd service file.
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
3. Edit `config/echobox.yaml` directly.
4. Run `./echobox fit`.
5. Start the local LLM server that matches `mlx_url`.
6. Run `./echobox demo`.
7. Manually apply the `trnscrb` patch instructions in `patches/README.md`, then run `./echobox watch`.

Important:

- `./install.sh` already creates `config/echobox.yaml`. If you run `./echobox setup` after `./install.sh`, the wizard exits early because the config already exists.
- Use `./echobox setup` only when the user wants the minimal interactive wizard and either has no config yet or is willing to delete `config/echobox.yaml` first.
- The files in `patches/*.diff` are not guaranteed to be applicable unified diffs. They are patch instructions for manual changes to the installed `trnscrb` source.

## Intelligent Setup (Agent Playbook)

For alpha users, treat intelligent setup as an agent-assisted playbook, not as silent product automation. The goal is to draft a good `config/echobox.yaml` faster, with explicit user consent around private data.

Recommended sequence:

1. Run `./install.sh` and `./echobox status` first.
2. Ask for consent before probing private sources like Calendar, Messages, Slack exports, or local notes.
3. Run `./echobox smart-setup` to probe the machine and draft context-source recommendations.
4. Interview the user about actual meeting types: investor, board, client, team sync, recruiting, support, and 1:1s.
5. If the user consents and a calendar CLI is available, run `./echobox smart-setup --with-calendar` to inspect recent event titles and attendee domains.
6. Edit `config/echobox.yaml` directly. Do not let the probe script silently rewrite it.
7. Review the suggested `context_sources.*`, `team.*`, and `meeting_types.*` values with the user before enabling them.

What to probe on macOS:

- Calendar CLIs: `gcalcli`, `gws`, `icalBuddy`
- Messaging sources: `~/Library/Messages/chat.db`, Slack.app presence, Slack export folders if the user points to them
- Project context: `PROJECT_DIR`, common project folders, notes directories, Obsidian presence
- Recording prerequisites: `ffmpeg`, `trnscrb`, BlackHole

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
| `echobox watch` | Start the `trnscrb` watcher on macOS |
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

- `whisper_model`: transcription model.
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
5. Verify the manual `trnscrb` patch instructions from `patches/README.md`.

Fast symptom mapping:

- Call detection never starts on macOS: inspect `trnscrb`, BlackHole, and the manual patch instructions.
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

  patches/
    README.md                      Manual patch instructions for `trnscrb`
    *.diff                         Human-readable patch descriptions

  docs/
    setup.md                       Detailed platform-specific setup
    context-sources.md             Context source configuration
    design-decisions.md            Architecture rationale
    troubleshooting.md             Troubleshooting reference
```

## Guardrails

- Do not modify `patches/*.diff` directly unless you are intentionally changing the patch instructions themselves.
- Treat `patches/*.diff` as documentation for manual `trnscrb` edits, not as guaranteed applicable patch files.
- `templates/report.html` uses CSS variables for theming; do not hardcode colors into the template.
- `config/echobox.yaml` may contain real passwords, tokens, and local paths; never commit user-specific values.

## See Also

`AGENTS.md` contains the same project context formatted for non-Claude AI agents.
