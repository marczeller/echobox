# Echobox

> **Status: Alpha** — for technical macOS users comfortable with CLI tools and manual setup
>
> **Alpha** — macOS only, requires manual setup

Echobox is a self-hosted call intelligence pipeline for macOS. It records calls, transcribes them with Whisper, identifies speakers, enriches the transcript with a local MLX server and project context, and publishes a clean HTML report you can search later.

Core processing stays on your machine: transcription, diarization, and enrichment run locally. Optional integrations such as web lookup, Claude-powered report generation, and Vercel publishing only make outbound requests when you enable them.

## Why Use It

- Record and process calls without sending raw transcripts to a SaaS by default
- Match recordings to calendar events and attendees for better speaker labeling
- Pull in context from documents, messages, and the web when useful
- Publish a readable HTML report and keep transcripts searchable over time
- Run on one machine or split recording and enrichment across two machines

## Status

| Area | macOS |
|------|:-----:|
| Transcription | Works |
| Speaker diarization | Works |
| Local MLX enrichment | Works |
| Calendar, docs, messages context | Works |
| Local HTML publishing | Works |
| Vercel publishing | Works |
| Auto call detection | Requires trnscrb + manual patches |

## Setup

> Requires: macOS (Apple Silicon), Homebrew, Python 3.12+

> Prerequisites:
> Apple Silicon, Homebrew, BlackHole, `trnscrb`, a HuggingFace token for pyannote, and a local MLX model/server.

```bash
git clone https://github.com/marczeller/echobox.git && cd echobox
./install.sh
```

1. Install dependencies and configure with `./install.sh`
2. Apply the manual `trnscrb` patch instructions in [patches/README.md](patches/README.md)
3. Start your MLX model server
4. Run `./echobox demo` to verify
5. Run `./echobox watch` to start recording

See a [sample report](docs/sample-report.html) generated from the demo fixtures.

## How It Works

| Stage | What Happens |
|-------|-------------|
| **1. Detection** | A watcher detects that a call has started or ended |
| **2. Recording** | System audio is captured to a WAV file |
| **3. Transcription** | faster-whisper transcribes the call locally |
| **4. Diarization** | pyannote.audio segments speakers |
| **5. Enrichment** | A local LLM receives the transcript plus project context |
| **6. Publishing** | A styled HTML report is generated locally or deployed |
| **7. Notification** | An optional webhook posts the finished report URL |

## Common Commands

| Command | Purpose |
|---------|---------|
| `echobox watch` | Run the watcher and process calls automatically on macOS |
| `echobox list` | Show recent calls and reports |
| `echobox open [report]` | Open the latest report or a named report |
| `echobox preview [call-or-file]` | Preview enrichment markdown in the terminal |
| `echobox search <term>` | Search transcripts and enrichments |
| `echobox actions` | Show action items across enriched calls |
| `echobox summary` | Show a weekly cross-call summary |
| `echobox reprocess <name>` | Re-run enrichment and publishing for a call |
| `echobox status` | Check whether the pipeline is configured correctly |
| `echobox config` | Show parsed config values |
| `echobox quality` | Run pipeline and context quality checks |
| `echobox fit` | Benchmark your hardware and recommend models |
| `echobox demo` | Run the pipeline walkthrough on sample data |
| `echobox test` | Run smoke tests |
| `echobox clean [--older N] [--prune]` | Show disk usage and optionally delete old data |

## Configuration

Main config lives in `config/echobox.yaml`. `./install.sh` creates it automatically if it does not exist.

Configured paths support `~` and environment-variable expansion.

Important settings:

- `whisper_model`, `mlx_model`, `mlx_url`: transcription model, enrichment model, and local MLX endpoint
- `context_sources`: calendar, messages, documents, and web integrations
- `team.members`, `team.internal_domains`, `team.roles`: speaker identity hints
- `meeting_types`: rules for classifying calls and choosing context
- `publish.engine`: `local` or `claude` HTML generation
- `publish.platform`, `publish.password`, `publish.scope`: local or Vercel publishing settings

For configuration examples, see [config/echobox.example.yaml](config/echobox.example.yaml) and [docs/context-sources.md](docs/context-sources.md).

## Diagnose Problems Quickly

Start with:

```bash
./echobox status
./echobox config
./echobox demo
```

Logs live in `~/echobox-data/logs/`.

For recording issues, patch requirements, and model-server problems, see [docs/troubleshooting.md](docs/troubleshooting.md).

## Where To Go Next

- Setup and install steps: [docs/setup.md](docs/setup.md)
- Context source configuration: [docs/context-sources.md](docs/context-sources.md)
- Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md)
- Patch details for `trnscrb`: [patches/README.md](patches/README.md)
- Architecture rationale: [docs/design-decisions.md](docs/design-decisions.md)

## License

MIT. See [LICENSE](LICENSE).
