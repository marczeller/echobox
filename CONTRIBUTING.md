# Contributing to Echobox

Echobox is an early-stage project. Contributions are welcome.

## Getting Started

```bash
git clone https://github.com/marczeller/echobox.git && cd echobox
./install.sh
./echobox.sh test
```

## Making Changes

1. Fork the repo and create a branch
2. Make your changes
3. Run `./echobox.sh test` — all tests must pass
4. Submit a pull request with a clear description

## Guidelines

- Keep changes focused — one fix or feature per PR
- Don't modify `patches/*.diff` files — they are applied verbatim to trnscrb
- `templates/report.html` uses CSS variables — don't hardcode colors
- Test on macOS (Apple Silicon) — that's the target platform
- No new dependencies without discussion

## Reporting Issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Output of `./echobox.sh status`
- macOS version and chip (M1/M2/M3/M4)
