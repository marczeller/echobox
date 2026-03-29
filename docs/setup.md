# Setup Guide

Echobox runs on **macOS (Apple Silicon)**.

## Before You Start

The real first-run sequence is:

```bash
./install.sh
./echobox status
./echobox smart-setup
./echobox demo
```

Then edit `config/echobox.yaml`, start the MLX server that matches `mlx_url`, and continue with recording setup.

Important:

- `./install.sh` already creates `config/echobox.yaml` if it does not exist.
- `./echobox setup` is optional. It only works as a wizard when no config exists yet. If `./install.sh` already created the config, either edit it directly or delete it before running `./echobox setup`.
- Path settings in `config/echobox.yaml` support `~` and environment-variable expansion.

## macOS Setup (Apple Silicon)

### Step 1: Install Echobox and Dependencies

```bash
git clone https://github.com/marczeller/echobox.git ~/echobox
cd ~/echobox
./install.sh
```

`./install.sh` checks for `ffmpeg`, `sounddevice`, Python 3.12+, `mlx-whisper`, `pyannote.audio`, BlackHole, and `HF_TOKEN`. It also creates `config/echobox.yaml`, runs the model fit flow unless your config already has model settings you keep, creates `~/echobox-data/*`, and writes an optional launchd service file.

If you are installing Python packages manually instead of using the installer, use:

```bash
python3.12 -m pip install --user mlx-whisper pyannote.audio pyyaml
```

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
./echobox status
./echobox config
```

If you want the minimal interactive config wizard instead of editing YAML directly, delete `config/echobox.yaml` first and then run `./echobox setup`.

### Step 5: Draft Context Sources and Meeting Types

Optional but recommended for agent-assisted setup:

```bash
./echobox smart-setup
```

If you want to inspect recent calendar titles and attendee domains too, get user consent first and then run:

```bash
./echobox smart-setup --with-calendar
```

This command is advisory. It does not rewrite `config/echobox.yaml`.

### Step 6: Edit `config/echobox.yaml`

At minimum, review:

- `whisper_model`
- `mlx_model`
- `mlx_url`
- `team.members`
- `context_sources.calendar.command`
- `publish.engine` and `publish.platform`

`./install.sh` now runs the hardware fit flow during setup on fresh installs, and on re-installs it only re-runs fit if you choose to or if a model setting is missing. Run `./echobox fit` manually any time you want to re-benchmark or change recommendations.

### Step 7: Start the LLM Server

**macOS (MLX):**

```bash
python3.12 -m pip install --user mlx-lm
mlx_lm.server --model <configured-mlx_model> --port 8090
```

### Step 8: Smoke-Test the Pipeline

```bash
./echobox demo
./echobox quality
```

### Step 9: Start Recording

```bash
./echobox watch
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
