# Setup Guide

Echobox runs on **macOS (Apple Silicon)**.

## Before You Start

The real first-run sequence is:

```bash
./install.sh
./echobox.sh status
./echobox.sh fit
./echobox.sh demo
```

Then edit `config/echobox.yaml`, start the MLX server that matches `mlx_url`, and continue with recording setup.

Important:

- `./install.sh` already creates `config/echobox.yaml` if it does not exist.
- `./echobox.sh setup` is optional. It only works as a wizard when no config exists yet. If `./install.sh` already created the config, either edit it directly or delete it before running `./echobox.sh setup`.
- Path settings in `config/echobox.yaml` support `~` and environment-variable expansion.

## macOS Setup (Apple Silicon)

### Step 1: Install Echobox and Dependencies

```bash
git clone https://github.com/marczeller/echobox.git ~/echobox
cd ~/echobox
./install.sh
```

`./install.sh` checks for `ffmpeg`, `trnscrb`, Python 3.12+, `faster-whisper`, `pyannote.audio`, BlackHole, and `HF_TOKEN`. It also creates `config/echobox.yaml`, `~/echobox-data/*`, and an optional launchd service file.

### Step 2: Configure BlackHole Audio

1. Open **Audio MIDI Setup**.
2. Click **+** and choose **Create Multi-Output Device**.
3. Enable both your speakers or headphones and **BlackHole 2ch**.
4. Set that Multi-Output Device as the system output in **System Settings > Sound**.

This lets you hear calls while BlackHole captures the audio for recording.

### Step 3: Set Up HuggingFace Token

1. Accept the pyannote model license: [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
2. Create a token: [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Export it:

```bash
echo 'export HF_TOKEN=hf_your_token_here' >> ~/.zshrc
source ~/.zshrc
```

### Step 4: Verify Current State

```bash
./echobox.sh status
./echobox.sh config
```

If you want the minimal interactive config wizard instead of editing YAML directly, delete `config/echobox.yaml` first and then run `./echobox.sh setup`.

### Step 5: Edit `config/echobox.yaml`

At minimum, review:

- `whisper_model`
- `mlx_model`
- `mlx_url`
- `team.members`
- `context_sources.calendar.command`
- `publish.engine` and `publish.platform`

### Step 6: Find the Best Models for Your Hardware

```bash
./echobox.sh fit
```

This writes recommended `whisper_model` and `mlx_model` values into `config/echobox.yaml`.

### Step 7: Start the LLM Server

**macOS (MLX):**

```bash
python3.12 -m pip install --user mlx-lm
mlx_lm.server --model <configured-mlx_model> --port 8090
```

### Step 8: Apply `trnscrb` Patch Instructions

Read [patches/README.md](../patches/README.md) and manually apply the six documented `trnscrb` changes. These files are instruction files, not guaranteed applicable unified diffs.

### Step 9: Smoke-Test the Pipeline

```bash
./echobox.sh demo
./echobox.sh quality
```

### Step 10: Start Recording

```bash
./echobox.sh watch
```

Or load the launchd service created by `./install.sh`:

```bash
launchctl load ~/Library/LaunchAgents/com.echobox.watcher.plist
```

## Two-Machine Setup

A laptop records and transcribes; a workstation runs the larger enrichment model.

### Laptop Setup

Follow the macOS setup above, then set the workstation target in either the environment or `config/echobox.yaml`:

```bash
echo 'export ECHOBOX_WORKSTATION=user@workstation.local' >> ~/.zshrc
source ~/.zshrc
```

Or:

```yaml
workstation_ssh: "user@workstation.local"
```

### Workstation Setup

1. Clone the repo.
2. Install Python 3.12+ and MLX.
3. Create `~/echobox-data/{transcripts,enrichments,reports,logs}`.
4. Start the MLX server.
5. Ensure SSH access from laptop to workstation.

Example with MLX:

```bash
python3.12 -m pip install --user mlx-lm
mlx_lm.server --model mlx-community/Qwen3-Next-80B-A3B-Instruct-6bit --port 8090
```

How it works:

1. The laptop records and transcribes locally.
2. The orchestrator syncs the transcript to the workstation.
3. The workstation runs enrichment even if the laptop is not running a local LLM server.
4. The enriched markdown is synced back for publishing.
