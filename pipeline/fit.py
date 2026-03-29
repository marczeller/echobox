#!/usr/bin/env python3
from __future__ import annotations
"""Hardware-aware model selection for Echobox.

Benchmarks Whisper models for transcription speed and uses LLMFit
to recommend MLX models for enrichment, based on your hardware.

Usage: python3 fit.py [--auto] [--whisper-only] [--mlx-only] [--dry-run]
"""
import argparse
import json
import os
import platform
import re
import resource
import subprocess
import sys
import shutil
import time
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "echobox.yaml"
EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "echobox.example.yaml"

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"


def ok(msg):
    print(f"  {GREEN}[ok]{NC} {msg}")


def warn(msg):
    print(f"  {YELLOW}[!!]{NC} {msg}")


def fail(msg):
    print(f"  {RED}[FAIL]{NC} {msg}")


def prompt_input(prompt: str, default: str | None = None) -> str:
    print(prompt, end="", flush=True)
    try:
        value = input().strip()
    except EOFError:
        message = [
            "Model fit is interactive and requires a TTY.",
            "Run './echobox fit --auto' for unattended selection, or re-run './echobox fit' in a terminal.",
        ]
        raise EOFError("\n".join(message)) from None
    if value or default is None:
        return value
    return default


def read_config_value(config_path: Path, key: str) -> str:
    if not config_path.exists():
        return ""
    for line in config_path.read_text().splitlines():
        match = re.match(rf"^{re.escape(key)}:\s*(.+)$", line)
        if match:
            return match.group(1).strip().strip("\"'")
    return ""


def _split_key_value_line(line: str) -> tuple[int, str, str] | None:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    match = re.match(r"^([A-Za-z0-9_.-]+):(?:\s*(.*))?$", stripped)
    if not match:
        return None
    indent = len(line) - len(stripped)
    return indent, match.group(1), match.group(2) or ""


def _replace_nested_key(content: str, key: str, value: str) -> str | None:
    parts = key.split(".")
    if len(parts) < 2:
        return None

    lines = content.splitlines()
    stack: list[tuple[str, int]] = []
    parent_indent: int | None = None
    insert_at: int | None = None

    for index, line in enumerate(lines):
        parsed = _split_key_value_line(line)
        if parsed is None:
            continue
        indent, current_key, _current_value = parsed
        while stack and indent <= stack[-1][1]:
            stack.pop()

        path = tuple(item[0] for item in stack + [(current_key, indent)])
        if path == tuple(parts):
            lines[index] = f"{' ' * indent}{current_key}: {value}"
            return "\n".join(lines) + ("\n" if content.endswith("\n") else "")

        if path == tuple(parts[:-1]):
            parent_indent = indent
            insert_at = index + 1
        elif parent_indent is not None and indent <= parent_indent:
            break

        stack.append((current_key, indent))
        if parent_indent is not None:
            insert_at = index + 1

    if parent_indent is not None and insert_at is not None:
        lines.insert(insert_at, f"{' ' * (parent_indent + 2)}{parts[-1]}: {value}")
        return "\n".join(lines) + ("\n" if content.endswith("\n") else "")

    return None


def write_config_value(config_path: Path, key: str, value: str) -> bool:
    if not config_path.exists():
        if EXAMPLE_CONFIG.exists():
            fail(f"Config not found. Run: cp {EXAMPLE_CONFIG} {config_path}")
        else:
            fail(f"Config not found: {config_path}")
        return False

    content = config_path.read_text()

    nested_content = _replace_nested_key(content, key, value)
    if nested_content is not None:
        config_path.write_text(nested_content)
        return True

    pattern = rf"^({re.escape(key)}:\s*).*$"
    new_content, count = re.subn(
        pattern,
        lambda match: f"{match.group(1)}{value}",
        content,
        flags=re.MULTILINE,
    )
    if count > 0:
        config_path.write_text(new_content)
        return True

    comment_pattern = rf"^#\s*({re.escape(key)}:\s*).*$"
    new_content, count = re.subn(
        comment_pattern,
        lambda match: f"{match.group(1)}{value}",
        content,
        flags=re.MULTILINE,
    )
    if count > 0:
        config_path.write_text(new_content)
        return True

    if not content.endswith("\n"):
        content += "\n"
    content += f"{key}: {value}\n"
    config_path.write_text(content)
    return True


def get_hardware_info() -> dict:
    info = {"chip": "Unknown", "memory_gb": 0}
    import platform

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info["chip"] = result.stdout.strip()
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info["memory_gb"] = int(result.stdout.strip()) / (1024 ** 3)
        except Exception:
            pass
        if info["chip"] == "Unknown" or info["memory_gb"] <= 0:
            try:
                result = subprocess.run(
                    ["system_profiler", "SPHardwareDataType"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("Chip:") and info["chip"] == "Unknown":
                            info["chip"] = stripped.split(":", 1)[1].strip()
                        elif stripped.startswith("Memory:") and info["memory_gb"] <= 0:
                            match = re.search(r"(\d+(?:\.\d+)?)\s*GB", stripped)
                            if match:
                                info["memory_gb"] = float(match.group(1))
            except Exception:
                pass
        if info["memory_gb"] <= 0:
            try:
                page_size = os.sysconf("SC_PAGE_SIZE")
                phys_pages = os.sysconf("SC_PHYS_PAGES")
                info["memory_gb"] = (page_size * phys_pages) / (1024 ** 3)
            except Exception:
                pass
        if info["chip"] == "Unknown":
            processor = platform.processor() or platform.machine()
            if processor:
                info["chip"] = processor
    else:
        pass

    return info


FALLBACK_MODELS = {
    16: "mlx-community/Qwen2.5-7B-Instruct-4bit",
    32: "mlx-community/Qwen2.5-14B-Instruct-4bit",
    64: "mlx-community/Qwen2.5-32B-Instruct-4bit",
    96: "mlx-community/Qwen3-Next-80B-A3B-Instruct-6bit",
}

WHISPER_MODELS = [
    "mlx-community/whisper-tiny-mlx",
    "mlx-community/whisper-base-mlx",
    "mlx-community/whisper-small-mlx",
    "mlx-community/whisper-medium-mlx",
    "mlx-community/whisper-large-v3-mlx",
]
SAMPLE_DURATION = 30.0


def install_llmfit() -> bool:
    try:
        result = subprocess.run(["brew", "install", "llmfit"], timeout=120)
        return result.returncode == 0
    except Exception:
        return False


def run_llmfit_recommend(limit: int = 20) -> list:
    try:
        result = subprocess.run(
            ["llmfit", "recommend", "--json", "--limit", str(limit)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("models", [])
    except Exception:
        return []


EMBEDDING_PATTERNS = ["embed", "nomic", "bge", "e5-", "gte-", "all-minilm", "sentence-"]
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"


def _is_chat_model(name: str) -> bool:
    lower = name.lower()
    for pat in EMBEDDING_PATTERNS:
        if pat in lower:
            return False
    return True


def _load_config(config_path: Path) -> dict:
    try:
        from pipeline.enrich import ConfigError
        from pipeline.enrich import load_config
    except ImportError:
        from enrich import ConfigError
        from enrich import load_config
    if not config_path.exists():
        return {}
    try:
        return load_config(config_path)
    except ConfigError:
        return {}


def _extract_param_count(name: str) -> float:
    matches = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[Bb](?!it)", name)
    if not matches:
        return 0.0
    try:
        return max(float(match) for match in matches)
    except ValueError:
        return 0.0


def _normalize_model_name(name: str) -> str:
    return name.split("/")[-1].strip().lower()


def _model_rank(model: dict) -> tuple:
    return (model.get("param_b", 0.0), model.get("size_gb", 0.0), model.get("name", ""))


def _hf_snapshot_dir(model_dir: Path) -> Path | None:
    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return None
    candidates = [path for path in snapshots.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _estimate_hf_model_size_gb(snapshot_dir: Path) -> float:
    tensor_files = list(snapshot_dir.rglob("*.safetensors"))
    if tensor_files:
        total_bytes = sum(tensor.stat().st_size for tensor in tensor_files)
        if total_bytes > 0:
            return total_bytes / (1024 ** 3)
        return len(tensor_files) * 2.0
    return 0.0


def detect_hf_cached_models() -> list:
    """Scan HF cache for MLX-friendly chat models."""
    found = []

    if not HF_CACHE.exists():
        return found

    for model_dir in HF_CACHE.iterdir():
        if not model_dir.is_dir() or not model_dir.name.startswith("models--"):
            continue

        snapshot_dir = _hf_snapshot_dir(model_dir)
        if not snapshot_dir:
            continue

        if next(snapshot_dir.rglob("config.json"), None) is None:
            continue

        parts = model_dir.name.replace("models--", "").split("--", 1)
        if len(parts) != 2:
            continue

        org, model = parts[0], parts[1]
        hf_id = f"{org}/{model}"
        found.append({
            "name": hf_id,
            "source": "huggingface",
            "size_gb": _estimate_hf_model_size_gb(snapshot_dir),
            "is_chat": _is_chat_model(hf_id),
            "param_b": _extract_param_count(hf_id),
            "start_cmd": f"mlx_lm.server --model {hf_id} --port 8090",
        })

    return sorted(found, key=_model_rank, reverse=True)




def detect_local_models() -> list:
    """Scan HuggingFace cache for downloaded MLX models."""
    return detect_hf_cached_models()


def _build_models_endpoints(config_path: Path) -> list:
    config = _load_config(config_path)
    configured = config.get("mlx_url", "").strip()
    endpoints = []
    seen = set()

    def add(endpoint: str):
        if not endpoint:
            return
        models_endpoint = endpoint.replace("/chat/completions", "/models")
        if models_endpoint in seen:
            return
        seen.add(models_endpoint)
        endpoints.append(models_endpoint)

    add(configured)

    defaults = [
        "http://localhost:8090/v1/chat/completions",
    ]

    for endpoint in defaults:
        add(endpoint)

    return endpoints


def detect_running_models(config_path: Path) -> list:
    found = []
    for endpoint in _build_models_endpoints(config_path, ):
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                models = data.get("data", data.get("models", []))
                for model in models:
                    name = model.get("id", model.get("name", "unknown"))
                    size_gb = model.get("size", 0) / (1024 ** 3) if model.get("size") else 0
                    found.append({
                        "name": name,
                        "endpoint": endpoint.replace("/models", "/chat/completions"),
                        "is_chat": _is_chat_model(name),
                        "size_gb": size_gb,
                        "param_b": _extract_param_count(name),
                    })
            if found:
                return sorted(found, key=_model_rank, reverse=True)
        except Exception:
            continue
    return found


def get_disk_free_gb() -> float:
    try:
        st = os.statvfs(os.path.expanduser("~"))
        return (st.f_bavail * st.f_frsize) / (1024 ** 3)
    except Exception:
        return 0.0


def run_mlx_fit(args, hw) -> str | None:
    print(f"  {BLUE}[1/2]{NC} {BOLD}MLX Enrichment Models{NC}")
    print()

    running = detect_running_models(Path(args.config))
    running_chat = [m for m in running if m["is_chat"]]
    running_embed = [m for m in running if not m["is_chat"]]

    local = detect_local_models()
    local_chat = [m for m in local if m["is_chat"]]
    hf_chat = [m for m in local_chat if m.get("source") == "huggingface"]
    running_names = {_normalize_model_name(m["name"]) for m in running_chat}

    if running or local_chat:
        print(f"  Already on this machine:")
        for m in running_chat:
            print(f"    {GREEN}[running]{NC} {m['name']} ({m['endpoint']})")
        for m in running_embed:
            print(f"    {YELLOW}[embed]{NC}   {m['name']} (not usable for enrichment)")
        for m in local_chat:
            if _normalize_model_name(m["name"]) not in running_names:
                print(f"    {BLUE}[cached]{NC}  {m['name']} ({m['size_gb']:.0f} GB, {m['source']})")
                print(f"              Start: {m['start_cmd']}")
        print()

    best_cached = local_chat[0] if local_chat else None
    best_hf = hf_chat[0] if hf_chat else None

    if best_cached and _normalize_model_name(best_cached["name"]) in running_names:
        ok(f"You're all set: {best_cached['name']} is already running")
        print()
        return best_cached["name"]

    if best_hf:
        print(f"  Best cached MLX model: {best_hf['name']} ({best_hf['size_gb']:.0f} GB)")
        print(f"  Start server: {best_hf['start_cmd']}")
        if args.auto:
            ok(f"Selected: {best_hf['name']}")
            print()
            return best_hf["name"]
        answer = prompt_input("  Use this model? [Y/n] ").lower()
        if answer in ("", "y", "yes"):
            ok(f"Selected: {best_hf['name']}")
            print()
            return best_hf["name"]
        print()

    if running_chat:
        best = running_chat[0]
        print(f"  Active chat model: {best['name']}")
        if not args.auto:
            answer = prompt_input("  Use this model? [Y/n] ").lower()
            if answer in ("", "y", "yes"):
                ok(f"Using: {best['name']}")
                print()
                return best["name"]
        else:
            ok(f"Using: {best['name']}")
            print()
            return best["name"]
        print()
    elif local_chat:
        best = local_chat[0]
        print(f"  Best cached model: {best['name']} ({best['size_gb']:.0f} GB)")
        print(f"  Not running. Start with: {best['start_cmd']}")
        if not args.auto:
            answer = prompt_input("  Use this model? [Y/n] ").lower()
            if answer in ("", "y", "yes"):
                ok(f"Selected: {best['name']}")
                print()
                return best["name"]
        else:
            ok(f"Selected: {best['name']}")
            print()
            return best["name"]
        print()

    disk_free = get_disk_free_gb()
    if disk_free > 0:
        print(f"  Disk space: {disk_free:.0f} GB free")

    has_llmfit = shutil.which("llmfit") is not None

    if not has_llmfit:
        if args.auto:
            warn("llmfit not found — installing via Homebrew")
            if not install_llmfit():
                warn("llmfit install failed — using fallback recommendations")
                return _fallback_mlx(args, hw)
        else:
            warn("llmfit not found (brew install llmfit)")
            answer = prompt_input("    Install now? [Y/n] ").lower()
            if answer in ("", "y", "yes"):
                if not install_llmfit():
                    warn("Install failed — using fallback recommendations")
                    return _fallback_mlx(args, hw)
            else:
                return _fallback_mlx(args, hw)

    models = run_llmfit_recommend()
    mlx_models = [m for m in models if m.get("runtime") == "MLX"]

    if disk_free > 0:
        before = len(mlx_models)
        mlx_models = [m for m in mlx_models if m.get("memory_required_gb", 0) * 1.2 <= disk_free]
        filtered = before - len(mlx_models)
        if filtered > 0:
            print(f"  ({filtered} model(s) excluded — won't fit on disk)")
            print()

    if not mlx_models:
        warn("No MLX-compatible models found via llmfit")
        return _fallback_mlx(args, hw)

    top = mlx_models[:5]

    print(f"  {'#':<3} {'Model':<45} {'Fit':>4} {'Speed':>6} {'Quality':>8} {'TPS':>5}")
    for i, m in enumerate(top, 1):
        sc = m.get("score_components", {})
        name = m["name"].split("/")[-1][:44]
        print(f"  {i:<3} {name:<45} {sc.get('fit',0):>4.0f} {sc.get('speed',0):>6.0f} {sc.get('quality',0):>8.0f} {m.get('estimated_tps',0):>5.0f}")

    rec_idx = 0
    for i, m in enumerate(top):
        sc = m.get("score_components", {})
        if sc.get("fit", 0) >= 80:
            rec_idx = i
            break

    rec = top[rec_idx]
    rec_name = rec["name"].split("/")[-1]
    print()
    print(f"  Recommendation: #{rec_idx+1} — {rec_name}")

    if args.auto:
        selected = rec
    else:
        choice = prompt_input(f"  Select [1-{len(top)}] or Enter for recommendation: ")
        if choice and choice.isdigit() and 1 <= int(choice) <= len(top):
            selected = top[int(choice) - 1]
        else:
            selected = rec

    model_name = selected["name"].split("/")[-1]
    model_id = f"mlx-community/{model_name}"

    print()
    ok(f"Selected: {model_id}")
    print(f"    Start server: mlx_lm.server --model {model_id} --port 8090")
    print()
    return model_id


def _fallback_mlx(args, hw) -> str | None:
    mem = hw.get("memory_gb", 16)
    best_tier = 16
    for t in sorted(FALLBACK_MODELS.keys()):
        if mem >= t:
            best_tier = t

    model = FALLBACK_MODELS[best_tier]
    print(f"  Fallback recommendation for {mem:.0f} GB: {model}")

    if not args.auto:
        answer = prompt_input("  Accept? [Y/n] ").lower()
        if answer not in ("", "y", "yes"):
            return None

    ok(f"Selected: {model}")
    print()
    return model


def generate_sample_wav() -> str | None:
    sample_path = "/tmp/echobox-fit-sample.wav"
    if os.path.exists(sample_path):
        return sample_path
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=440:duration={int(SAMPLE_DURATION)}",
             "-ar", "16000", "-ac", "1", sample_path],
            capture_output=True, timeout=15
        )
        if result.returncode == 0:
            return sample_path
    except Exception:
        pass
    return None


def benchmark_whisper_model(model_size: str, sample_path: str) -> dict | None:
    try:
        import mlx_whisper
    except ImportError:
        return None

    result = {"model": model_size}

    t0 = time.time()
    try:
        transcript = mlx_whisper.transcribe(sample_path, path_or_hf_repo=model_size)
    except Exception as e:
        result["error"] = str(e)
        return result
    result["transcribe_time"] = time.time() - t0
    result["rtf"] = result["transcribe_time"] / SAMPLE_DURATION
    result["segments"] = len(transcript.get("segments", [])) if isinstance(transcript, dict) else 0

    # macOS ru_maxrss is in bytes
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result["peak_memory_mb"] = usage.ru_maxrss / (1024 * 1024)

    return result


def run_whisper_fit(args) -> str | None:
    print(f"  {BLUE}[2/2]{NC} {BOLD}Whisper Transcription Models{NC}")
    print()

    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        fail("ffmpeg not found — install: brew install ffmpeg")
        return None

    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        fail("mlx-whisper not found — install: pip install mlx-whisper")
        return None

    sample_path = generate_sample_wav()
    if not sample_path:
        fail("Could not generate sample audio")
        return None

    print(f"  Benchmarking {len(WHISPER_MODELS)} models on {SAMPLE_DURATION:.0f}s sample...")
    print()

    results = []
    for model_size in WHISPER_MODELS:
        print(f"    Testing {model_size}...", end=" ", flush=True)
        r = benchmark_whisper_model(model_size, sample_path)
        if r and "error" not in r:
            print(f"RTF {r['rtf']:.2f}")
            results.append(r)
        elif r and "error" in r:
            print(f"SKIP ({r['error'][:40]})")
        else:
            print("SKIP (import error)")

    if not results:
        fail("No Whisper models benchmarked successfully")
        return None

    print()
    print(f"  {'Model':<40} {'Transcribe':>11} {'RTF':>6} {'Memory':>8}")
    for r in results:
        mem_str = f"~{r['peak_memory_mb']:.0f} MB"
        print(f"  {r['model']:<40} {r['transcribe_time']:>10.1f}s {r['rtf']:>6.2f} {mem_str:>8}")

    recommended = None
    for r in reversed(results):
        if r["rtf"] < 0.5:
            recommended = r
            break
    if not recommended:
        recommended = results[0]

    print()
    print(f"  Recommendation: {recommended['model']} (RTF {recommended['rtf']:.2f})")

    if args.auto:
        selected = recommended
    else:
        models_str = "/".join(r["model"] for r in results)
        choice = prompt_input(f"  Select [{models_str}] or Enter for recommendation: ").lower()
        matched = [r for r in results if r["model"] == choice]
        selected = matched[0] if matched else recommended

    print()
    ok(f"Selected: {selected['model']}")
    print()
    return selected["model"]


def main():
    parser = argparse.ArgumentParser(description="Echobox model fit — find the best models for your hardware")
    parser.add_argument("--auto", action="store_true", help="Accept all recommendations without prompting")
    parser.add_argument("--whisper-only", action="store_true", help="Only benchmark Whisper models")
    parser.add_argument("--mlx-only", action="store_true", help="Only run MLX model selection")
    parser.add_argument("--dry-run", action="store_true", help="Show recommendations without writing config")
    parser.add_argument("--config", "-c", default=str(DEFAULT_CONFIG), help="Config file path")
    args = parser.parse_args()

    print()
    print("  ╔═══════════════════════════════════════╗")
    print("  ║         ECHOBOX MODEL FIT            ║")
    print("  ╚═══════════════════════════════════════╝")
    print()

    hw = get_hardware_info()
    print(f"  Hardware: {hw['chip']} · {hw['memory_gb']:.0f} GB unified memory")
    print()

    config_path = Path(args.config)

    try:
        if not args.whisper_only:
            mlx_model = run_mlx_fit(args, hw)
            if mlx_model and not args.dry_run:
                write_config_value(config_path, "mlx_model", mlx_model)

        if not args.mlx_only:
            whisper_model = run_whisper_fit(args)
            if whisper_model and not args.dry_run:
                write_config_value(config_path, "whisper_model", whisper_model)
    except EOFError as exc:
        print()
        fail(str(exc))
        return 1

    if not args.dry_run:
        print()
        ok("Config updated: config/echobox.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
