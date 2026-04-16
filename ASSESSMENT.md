# Defensive Programming Assessment

Scope: every `try/except` in Python and `|| true` / `2>/dev/null` in shell scripts, classified KEEP vs REMOVE.

## Headline finding

**The codebase is already well-hardened.** The recent "harden against Codex red-team findings" commit (f92dcd3) already narrowed or removed the worst patterns. The remaining body is overwhelmingly legitimate boundary handling (subprocess, network, audio, external data, pyobjc/rumps AppKit calls). No bare `except Exception: pass` wraps a purely internal function.

The dominant improvement opportunity is **narrowing `except Exception` to specific types** at boundaries where the expected failure mode is obvious. `except Exception` is a weaker version of defensive programming that still catches all bugs — narrowing it surfaces real bugs while preserving the legitimate boundary.

I treat those narrowings as the "removals" — the blanket handler is removed, replaced by a targeted one.

## Shell patterns

All `|| true` and `2>/dev/null` occurrences reviewed. All KEEP:

- `orchestrator.sh` `chmod 600` / `chmod 700` on notify log — the pipeline must not die if chmod fails on a weird filesystem. KEEP.
- `orchestrator.sh` `rsync -az ... 2>/dev/null || true` on sidecar fetch — sidecar is optional, absence is fine. KEEP.
- `orchestrator.sh` `mkdir -p ${MEETING_NOTES_DIR}/... 2>/dev/null || true` — remote dir may already exist; ssh success with no error. KEEP.
- `orchestrator.sh` `slug_from_enrichment.py ... 2>/dev/null || echo ""` — orchestrator documents empty stdout as "keep legacy slug". KEEP.
- `orchestrator.sh` `cat ... || echo ""` for last-report-url — absent state file is normal. KEEP.
- `orchestrator.sh` `grep -A 3 ... 2>/dev/null | tail | cut` — summary grep against potentially-missing section is optional. KEEP.
- `publish.sh` / `orchestrator.sh` `read_config` via heredoc with `2>/dev/null || true` — config reader has its own error path; silent fall-through to default is correct. KEEP.
- `install.sh` `grep -oE ... || true` on Python version probe. KEEP.
- `calendar.sh` `bash -c "$CMD" 2>/dev/null || echo '{"items":[]}'` — keep a valid JSON contract with the caller. KEEP.
- `quality/*.sh` uses `2>/dev/null` extensively — these are intentionally best-effort health probes. KEEP.
- `publish.sh:111` `claude -p ... 2>/dev/null` — suppresses benign claude CLI chatter; downstream error surfaces via empty output + size check. KEEP.

## Python: KEEP (selected highlights)

All of the following are legitimate and left alone:

- `echobox_recorder/recorder.py`: every handler — vendored code, audio I/O, rate-ladder retry on AirPods SCO, diarization model load, VAD fallback, cleanup teardown chains. Explicitly called out in CLAUDE.md as intentional. **All KEEP.**
- `echobox_recorder/watcher.py`: subprocess + CoreAudio FFI + SIGINT handler. KEEP.
- `echobox_recorder/menubar.py`: wraps rumps/pyobjc (macOS framework) and subprocess. The AppKit layer can raise in ways not documented anywhere. KEEP.
- `echobox_recorder/swift_helper.py`: subprocess lifecycle management for the Swift capture helper. KEEP.
- `echobox_recorder/caption_panel.py`: AppKit/pyobjc guard. KEEP.
- `pipeline/enrich.py`: YAML parse (`yaml.safe_load`), `urllib` to local MLX server, `ssh`/`local_run` subprocess, calendar JSON parse, optional template file read, `sqlite3` against Messages.app, `load_prompt_template` template validation. All boundary handling. `_sanitize_prompt_field` / `_sanitize_context_term` called out as security-critical. KEEP.
- `pipeline/speaker_id.py`: numpy/pyannote import guards (required by CLAUDE.md flow), torch MPS detect, embedding load, inference against potentially-corrupt WAV, per-voice JSON parse. KEEP.
- `pipeline/serve.py`: socket bind, HTTP parse, tailscale/bore subprocess, rate-limit state. KEEP.
- `pipeline/demo.py`, `pipeline/setup.py`, `pipeline/show_config.py`, `pipeline/read_config.py`: `ConfigError` handling at the CLI boundary — user-facing, correct. KEEP.
- `pipeline/fit.py:278-286` module import fallback / ConfigError swallow in `_load_config` — deliberate fallback; config errors already surface via `echobox status`. KEEP.
- `pipeline/clean.py`: `OSError` narrowed already, `ValueError` around date-format parsing. KEEP.
- `pipeline/slug_from_enrichment.py:76` `except Exception: print("")` — correct: sidecar contract is "empty stdout means fall back to legacy slug", including on malformed JSON. KEEP.
- `echobox.py:182` `can_write_directory` `except Exception` — Path operations through user-configured locations; several exception types possible. Marginal narrowing value. KEEP.
- `echobox.py:398` reaper `except Exception: pass` — subprocess.wait() has documented weird failure modes when processes are already reaped. KEEP.
- `echobox.py:779` `except Exception` around `wave.open` — the WAV may be truncated / non-RIFF. KEEP.
- `pipeline/markdown_preview.py:21` `OSError` already narrow. KEEP.
- `pipeline/setup.py`, `pipeline/fit.py` `EOFError` handlers around `input()` — TTY-absent is real. KEEP.
- `pipeline/serve.py:344` `except Exception: tunnel_proc.kill()` — best-effort cleanup on shutdown. KEEP.
- `pipeline/serve.py:368` `except Exception: pass` in `_start_tailscale` — wraps tailscale subprocess + JSON parse. Narrowing possible but handler is called out as cleanup-on-failure → empty string. Low value. KEEP.
- `pipeline/enrich.py:589` sqlite Messages.app query handler — external DB, KEEP.
- `pipeline/enrich.py:1110` curl urlopen check — network. KEEP.
- `pipeline/enrich.py:1180` template placeholder validation — external user data. KEEP.
- Test files: `except ModuleNotFoundError` for optional imports — correct. KEEP.

## Python: REMOVE (narrow `except Exception` to specific types)

Each of these catches more than it needs, hiding real bugs. The function's intent is "if the probe/import/parse fails for *its* reason, fall back"; `except Exception` also catches `KeyboardInterrupt`-derivative issues, `AttributeError` from logic bugs, etc.

High-confidence:

| # | Location | Current | Narrowed to | Rationale |
|---|----------|---------|-------------|-----------|
| 1 | `pipeline/status.py:22` `command_output` | `except Exception` | `except (OSError, subprocess.SubprocessError)` | Only subprocess failure is expected |
| 2 | `pipeline/status.py:31` `module_importable` | `except Exception` | `except (ImportError, ModuleNotFoundError)` | Only import failure is expected |
| 3 | `pipeline/status.py:45` `can_reach_models` | `except Exception` | `except (OSError, subprocess.SubprocessError)` | curl subprocess boundary |
| 4 | `pipeline/status.py:59` `can_reach_ssh` | `except Exception` | `except (OSError, subprocess.SubprocessError)` | ssh subprocess boundary |
| 5 | `pipeline/status.py:75` `is_writable` | `except Exception` | `except OSError` | mkdir/write/unlink |
| 6 | `pipeline/fit.py:178,187,204,211` `get_hardware_info` | `except Exception: pass` (x4) | `except (OSError, subprocess.SubprocessError, ValueError): pass` | sysctl/system_profiler subprocess + int() cast |
| 7 | `pipeline/fit.py:244` `install_llmfit` | `except Exception` | `except (OSError, subprocess.SubprocessError)` | brew subprocess |
| 8 | `pipeline/fit.py:258` `run_llmfit_recommend` | `except Exception` | `except (OSError, subprocess.SubprocessError, json.JSONDecodeError)` | subprocess + JSON parse |
| 9 | `pipeline/fit.py:417` `detect_running_models` | `except Exception: continue` | `except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, KeyError): continue` | HTTP + JSON + dict access |
| 10 | `pipeline/fit.py:426` `get_disk_free_gb` | `except Exception` | `except OSError` | statvfs only raises OSError |
| 11 | `pipeline/fit.py:616` `generate_sample_wav` | `except Exception: pass` | `except (OSError, subprocess.SubprocessError): pass` | ffmpeg subprocess |
| 12 | `pipeline/smart_setup.py:69` `run_command` | `except Exception as exc` | `except (OSError, subprocess.SubprocessError) as exc` | subprocess |
| 13 | `pipeline/smart_setup.py:81` `readable_sqlite` | `except Exception` | `except (OSError, sqlite3.Error)` | Only sqlite connect/exec raises these |
| 14 | `pipeline/smart_setup.py:89` `module_exists` | `except Exception` | `except (ImportError, ModuleNotFoundError)` | importlib |
| 15 | `pipeline/search.py:18` scan read | `except Exception: continue` | `except (OSError, UnicodeDecodeError): continue` | File read on arbitrary user transcripts |

Stopping at 15 per budget.

## Uncertain (overflow — worth narrowing but not included)

- `pipeline/enrich.py:752` `_fetch_prior_meetings` — could narrow to `(OSError, json.JSONDecodeError, KeyError, TypeError)`. Sidecar JSON from older runs. Low risk but tests touch this path.
- `pipeline/fit.py:632` `except Exception as e` around llmfit json — likely safe to narrow to `json.JSONDecodeError, ValueError, OSError`. Not in high-confidence list only because I haven't read the llmfit CLI spec.
- `pipeline/serve.py:368` `_start_tailscale` blanket except — mostly harmless, narrowing is aesthetic.
- `pipeline/smart_setup.py:108` already `OSError` — no-op.
- `echobox.py:182` `can_write_directory` — small refactor, leave for now.

## Deliverable summary

- ASSESSMENT.md (this file).
- 15 narrowings applied in `pipeline/status.py`, `pipeline/fit.py`, `pipeline/smart_setup.py`, `pipeline/search.py`.
- No code shape changes (caller contracts preserved); tests should stay green.
- Baseline: `./echobox test` has 1 pre-existing failure (`test_recorder`) caused by `sounddevice` not being importable in this environment — unrelated to this work.
