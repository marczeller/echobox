# Design Decisions

This document explains the architectural choices behind Echobox and why each decision was made.

## Summary

| Decision | Alternatives Considered | Why We Chose This |
|----------|------------------------|-------------------|
| Browser-first meeting detection | Native app detection only | Most calls happen in browsers; native-only misses them |
| WAV retention after transcription | Delete after transcription | Diarization models improve; re-processing needs original audio |
| Calendar-based meeting classification | Transcript analysis, manual tagging, audio fingerprinting | Calendar events provide the richest structured metadata |
| Attendee injection into LLM prompt | Let LLM guess speakers from content | Real names from calendar prevent misidentification |
| OAuth-only for Claude CLI | API key auth | No unexpected billing; no key management |
| Pluggable context sources via shell commands | Built-in SaaS integrations | Works with any tool that has a CLI; no vendor lock-in |
| Local MLX enrichment | Cloud LLM APIs | Privacy-first; transcripts never leave your machine |
| Cookie-based auth gate | User accounts, OAuth flows, database sessions | Simple single-password HMAC cookie; no database needed |
| YAML configuration | Environment variables, JSON, TOML | Supports comments, human-readable, handles nested structures |
| Separate enrichment and publishing stages | Single monolithic pipeline | Re-publish without re-enriching; debug stages independently |
| Two-machine architecture (laptop + workstation) | Single-machine only | Best recording machine differs from best enrichment machine |
| Hardware-aware model selection via LLMFit | Hardcoded model recommendations, manual benchmarking | Apple Silicon memory varies 10x; LLMFit scores 500+ models against actual hardware |
| AI agent project files (CLAUDE.md + AGENTS.md) | README only, wiki | Most users will share the repo URL with their AI assistant; these files give instant comprehension |

## Browser-First Meeting Detection

**Decision:** Check browser tabs for meeting URLs before checking for native apps.

**Reasoning:** The majority of calls happen in web browsers -- Google Meet, Zoom Web, Microsoft Teams Web. The original upstream watcher only watched native apps (Zoom.app, Teams.app, FaceTime), which misses most calls. By querying browser tabs via AppleScript first, we catch the common case before falling through to native app detection.

**Trade-off:** AppleScript queries add a small latency (~200ms) to each detection cycle. Acceptable given detection runs every few seconds.

## Vendored Recording Subsystem

**Decision:** Vendor the minimal recorder and watcher modules directly into `echobox_recorder/` instead of relying on an external recorder install plus manual patching.

**Reasoning:** Manual recorder patching was the largest setup failure point. Vendoring the minimal macOS recording subsystem removes install drift, makes `./echobox watch` work after `./install.sh`, and keeps the integration points inside the Echobox codebase.

**Trade-off:** Echobox now owns a small amount of platform-specific recording code and must preserve the upstream attribution notice.

## WAV Retention

**Decision:** Keep WAV audio files after transcription instead of deleting them.

**Reasoning:** Speaker diarization models improve rapidly. A recording from today might benefit from a better pyannote model released next month. If the audio is deleted, the only option is "it's fine as-is." Disk space for WAV files is negligible (a 1-hour call at 44.1kHz mono is ~300 MB) compared to the cost of losing irreplaceable audio.

**Trade-off:** Disk usage increases over time. Users who do not want indefinite retention can prune old WAV files with `./echobox clean --older N`.

## Calendar-Based Meeting Classification

**Decision:** Use calendar events as the primary signal for meeting type classification.

**Reasoning:** Calendar events are the richest structured data source for meetings. They contain:
- **Title** -- patterns like "sync", "client", "standup" classify the meeting type
- **Attendees** -- email addresses map to real names for speaker identification
- **Timestamp** -- matches the recording to the correct event
- **Description** -- sometimes contains agendas or context links

Alternatives considered:
- **Transcript content analysis** -- requires the LLM to classify before having context, creating a chicken-and-egg problem
- **Manual classification** -- defeats the purpose of automation
- **Audio fingerprinting** -- doesn't provide attendee information

## Attendee Injection into LLM Prompt

**Decision:** Extract attendee names from calendar events and include them in the LLM prompt.

**Reasoning:** Without real names, the LLM must guess speaker identity from conversation content alone. This leads to frequent misidentification, especially when speakers discuss third parties. By injecting a list of known attendees with roles, the LLM can confidently map "SPEAKER_01 who talks about architecture" to the person whose role is "Architecture Lead."

**Trade-off:** Requires a configured calendar source with attendee data. Falls back to team member list from config if no calendar match.

## OAuth-Only for Claude CLI

**Decision:** Set `ANTHROPIC_API_KEY=""` explicitly when calling the Claude CLI.

**Reasoning:** The Claude CLI supports two auth methods: API key (pay-per-token) and OAuth (uses your existing subscription). By explicitly blanking the API key, we force OAuth mode. This means:
- No unexpected billing from automated pipeline runs
- Uses the same quota as your interactive Claude usage
- No API key to manage, rotate, or accidentally commit

**Trade-off:** Requires `claude auth login` to be run manually once. Token refresh can fail in non-interactive sessions (see troubleshooting).

## Pluggable Context Sources

**Decision:** All context sources (calendar, messages, documents, web) are configured via shell commands in YAML.

**Reasoning:** Every team has a different toolchain. Some use Slack, others Telegram. Some have Notion, others use plain files. Rather than building integrations for each service, Echobox accepts any shell command that produces text output. This means:
- No dependency on specific SaaS APIs
- Works with any tool that has a CLI
- Users can write custom scripts and plug them in
- No vendor lock-in

**Trade-off:** Configuration requires more thought than a "connect to Slack" button. The setup guide and examples mitigate this.

## Local MLX Enrichment

**Decision:** Run LLM enrichment on local Apple Silicon hardware via MLX, not cloud APIs.

**Reasoning:** Call transcripts contain sensitive information -- strategic discussions, personnel matters, financial details. Sending them to cloud APIs creates privacy and compliance risks. MLX on Apple Silicon provides:
- **Privacy** -- transcript content never leaves your machines
- **Cost** -- no per-token charges, just electricity
- **Speed** -- Apple Silicon's unified memory means large models run fast
- **Availability** -- no API rate limits or outages

**Trade-off:** Requires Apple Silicon hardware with sufficient memory. A 70B model needs 64+ GB unified memory. Smaller models (7B-8B) work on 16 GB but produce lower-quality enrichments.

## Cookie-Based Auth Gate

**Decision:** Password protection uses a simple cookie with HMAC validation.

**Reasoning:** The auth gate needs to protect reports from unauthorized access without requiring user accounts, databases, or OAuth flows. A single shared password with a signed cookie provides:
- **Simplicity** -- one password, one cookie, works everywhere
- **No database** -- token validation is cryptographic, not a DB lookup
- **Stateless** -- any Vercel edge can validate the cookie
- **Good enough** -- these are internal reports, not banking apps

**Trade-off:** Single password shared among all viewers. No per-user access control. Acceptable for small teams sharing call reports.

## YAML Configuration

**Decision:** Use YAML for all configuration instead of environment variables, JSON, or TOML.

**Reasoning:** YAML supports comments (unlike JSON), is human-readable (unlike env vars for complex structures), and is widely understood. Configuration for context sources involves nested structures with commands, paths, and queries -- YAML handles this naturally. The config file can be version-controlled alongside the code.

**Trade-off:** Echobox now depends on PyYAML for config parsing. That adds one Python dependency, but it eliminates the nested-configuration bugs and ambiguity of the previous hand-rolled parser.

## Separate Enrichment and Publishing

**Decision:** Enrichment (MLX) and publishing (HTML generation + deployment) are independent pipeline stages.

**Reasoning:** Enrichment is compute-intensive (minutes on large models). Publishing is fast (seconds). By keeping them separate:
- **Re-publish** without re-enriching when you change the report template
- **Re-enrich** a transcript with a better model without re-publishing
- **Debug** each stage independently
- **Skip stages** when they're unnecessary (e.g., no Vercel for local-only use)

## Two-Machine Architecture

**Decision:** Support a laptop-records, workstation-enriches split.

**Reasoning:** The best recording machine (your laptop, present in meetings) is rarely the best enrichment machine (a workstation with 128 GB unified memory). Separating these roles via SSH + rsync lets each machine do what it's best at. The orchestrator handles the coordination automatically.

**Trade-off:** Requires SSH access between machines. Network issues can delay enrichment. The pipeline degrades gracefully -- if the workstation is unreachable, raw transcripts are preserved for later enrichment.
