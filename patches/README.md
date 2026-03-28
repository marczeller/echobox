# Echobox Patches for trnscrb

Echobox requires 6 patches to trnscrb to enable pipeline integration. These patches are described as diff files in this directory.

**trnscrb installs via Homebrew**, so the exact file paths depend on your version. Find your trnscrb package directory:

```bash
python3 -c "import trnscrb, pathlib; print(pathlib.Path(trnscrb.__file__).parent)"
```

## Patch Summary

| # | Patch File | Target File | Description |
|---|-----------|-------------|-------------|
| 1 | `01-browser-first-detection.diff` | `watcher.py` | Check browser tabs for meeting URLs (Meet, Zoom, Teams) before native app detection. Most calls happen in browsers; native-only detection misses them. |
| 2 | `02-wav-retention.diff` | `cli.py` | Keep WAV audio files after transcription instead of deleting them. Enables re-running diarization with improved models later. |
| 3 | `03-on-stop-hook.diff` | `cli.py` | Add `--on-stop` CLI flag that executes a configurable command when recording stops. This triggers the Echobox pipeline automatically. |
| 4 | `04-output-path.diff` | `cli.py` | Add `--output-dir` flag to write transcripts to a configurable directory instead of the default location. |
| 5 | `05-diarization-fix.diff` | `transcribe.py` | Fix pyannote pipeline initialization on Apple Silicon. Ensures the diarization model loads correctly with MPS backend. |
| 6 | `06-blackhole-default.diff` | `audio.py` | Default to BlackHole virtual audio device for system audio capture instead of requiring manual device selection. |

## Applying Patches

The `install.sh` script lists these patches but cannot apply them automatically because trnscrb install paths vary by system and version.

### Manual Application

1. Find your trnscrb package directory (see command above)
2. Read each patch file for the change description
3. Apply the changes to the corresponding source file
4. Restart trnscrb

### After trnscrb Upgrades

Patches need to be re-applied after upgrading trnscrb via Homebrew. Run `install.sh` again to check patch status.

## Patch Details

Each patch file contains a human-readable description of the change, not a literal unified diff. This is intentional: trnscrb's source changes between versions, so a static diff would break. The descriptions tell you what to change and where.
