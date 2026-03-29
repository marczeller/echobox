# Troubleshooting

## First Triage

Start every diagnosis with:

```bash
./echobox status
./echobox config
./echobox demo
```

What each one tells you:

- `status`: missing dependencies, missing config, server reachability, data directories
- `config`: the parsed values Echobox is actually reading
- `demo`: whether prompt building and report preview work even without live recording

Logs live in `~/echobox-data/logs/`:

- `watcher.log`
- `pipeline.log`
- `echobox.log`
- `echobox.err`

## Recording Issues

### The built-in watcher does not detect my call on macOS

**Symptoms:** Call starts but recording never begins.

Check these in order:

1. `./echobox status` should show the recorder package and `sounddevice` importable, and BlackHole present.
2. Confirm BlackHole is part of your Multi-Output Device in Audio MIDI Setup.
3. Confirm the system output device is that Multi-Output Device.
4. Check `~/echobox-data/logs/watcher.log` for browser detection or recorder errors.

### Recording captures only my voice on macOS

You are likely recording the microphone instead of system audio.

Fix:

1. Install BlackHole: `brew install blackhole-2ch`
2. Create a Multi-Output Device with your speakers plus BlackHole.
3. Set that Multi-Output Device as the active output device.

### WAV files disappear after transcription

Echobox now keeps WAV files by default. If they are missing, check `~/echobox-data/transcripts` and `~/echobox-data/logs/watcher.log` for recorder or filesystem errors.

## Transcription Issues

### Whisper model download fails

`mlx-whisper` downloads models on first use. If the `mlx-community/whisper-large-v3-mlx` cache does not appear after `./echobox fit` or your first transcription, retry manually:

```bash
python3.12 -c "import mlx_whisper; mlx_whisper.transcribe('/tmp/echobox-fit-sample.wav', path_or_hf_repo='mlx-community/whisper-large-v3-mlx')"
```

Or temporarily choose a smaller model in `config/echobox.yaml`:

```yaml
whisper_model: mlx-community/whisper-medium-mlx
```

### Poor transcription quality

Check:

- Audio quality and routing first
- `whisper_model` in `config/echobox.yaml`
- Whether your transcript source is using the expected model or backend

If unsure, run `./echobox fit` and re-check the resulting `whisper_model`.

## Diarization Issues

### pyannote fails to load

Common causes:

1. `HF_TOKEN` is missing.
2. The pyannote model license was not accepted.
3. Your Python environment does not have `pyannote.audio` installed.
4. The local Python environment cannot initialize the diarization pipeline correctly on this machine.

Checks:

```bash
./echobox status
python3.12 -c "import pyannote.audio; print('ok')"
```

### Speakers are still `SPEAKER_00`, `SPEAKER_01`

That usually means diarization succeeded but identity mapping was weak.

Check:

- `team.members`
- `team.internal_domains`
- `team.roles`
- `context_sources.calendar.command`
- Whether the meeting matched a calendar event

## Enrichment Issues

### Model server is not responding

Symptoms:

- `./echobox status` reports `MLX server: NOT RUNNING`
- `enrich` fails or falls back to limited output

Check in order:

1. Confirm `mlx_url` in `config/echobox.yaml`.
2. Confirm the server is actually running.
3. Test the models endpoint directly:

```bash
curl -sf http://localhost:8090/v1/models
```

Typical endpoint:

- MLX: `http://localhost:8090/v1/chat/completions`

### Wrong model is being used

Edit `config/echobox.yaml` and verify both of these:

```yaml
mlx_model: your-model-name
mlx_url: http://your-server/v1/chat/completions
```

Then restart the server and run `./echobox status`.

If you meant transcription model rather than enrichment model, edit `whisper_model` instead.

### MLX out of memory

Your configured model is too large for available memory. Run:

```bash
./echobox fit
```

Or set a smaller `mlx_model` manually and restart the server.

### Enrichment is slow

Check:

- Model size versus available RAM or VRAM
- Whether you should switch to a smaller model
- Whether a two-machine setup makes more sense

## Publishing Issues

### Claude CLI not found

`publish.engine: claude` requires the Claude CLI. If it is missing, Echobox falls back to local HTML generation.

Install and authenticate:

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

### Vercel deploy fails

Check these separately:

1. `publish.platform: vercel`
2. `publish.password` is not empty or `change-me`
3. `vercel` CLI is installed and authenticated
4. `publish.scope` is correct if you deploy into a team scope

If deploy still fails, switch to local publishing first:

```yaml
publish.platform: local
```

### Report looks wrong or raw markdown is visible

That usually means the local template fallback rendered the markdown instead of Claude-generated custom HTML.

Check:

- `publish.engine`
- Whether `claude` CLI is installed
- Whether the local fallback is acceptable for the user

Re-run publish explicitly:

```bash
./echobox publish ~/echobox-data/enrichments/your-file-enriched.md
```

## Two-Machine Issues

### SSH connection to workstation fails

Check:

```bash
ssh user@workstation.local echo ok
```

Then verify either:

- `ECHOBOX_WORKSTATION`
- `workstation_ssh` in `config/echobox.yaml`

### `rsync` fails

Check:

- `rsync` is installed on both machines
- SSH connectivity works
- The target directories exist on the workstation

## Notification Issues

### Notification not sent

Check:

- `notify.enabled: true`
- `notify.command` actually works when run by itself

A quick safe test is:

```yaml
notify:
  enabled: true
  command: "echo \"$(date -Iseconds) $ECHOBOX_REPORT_TITLE $ECHOBOX_REPORT_URL\" >> ~/echobox-notifications.log"
```

## Quality Check Failures

Run:

```bash
./echobox quality
./echobox test
```

`quality` checks pipeline readiness and context quality. `test` runs the Python smoke tests in `tests/`.
