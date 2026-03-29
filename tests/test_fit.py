#!/usr/bin/env python3
"""Smoke tests for pipeline/fit.py hardware detection and config writing."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    shim_dir = Path(tempfile.mkdtemp(prefix="echobox-yaml-shim-"))
    (shim_dir / "yaml.py").write_text(
        "import json\n\ndef safe_load(stream):\n    return json.loads(stream.read())\n"
    )
    sys.path.insert(0, str(shim_dir))

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.fit import _build_models_endpoints
from pipeline.fit import _extract_param_count
from pipeline.fit import detect_local_models
from pipeline.fit import get_hardware_info
from pipeline.fit import read_config_value
from pipeline.fit import write_config_value

PASS = 0
FAIL = 0


def subprocess_result(returncode: int, stdout: str, stderr: str):
    class Result:
        pass

    result = Result()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def check(condition: bool, label: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def main():
    hw = get_hardware_info()
    check(hw["memory_gb"] > 0, f"memory detected: {hw['memory_gb']:.1f} GB")
    check(hw["chip"] != "Unknown", f"chip detected: {hw['chip']}")

    fake_profiler = "Hardware:\n\n    Hardware Overview:\n\n      Chip: Apple M4 Max\n      Memory: 128 GB\n"
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess_result(1, "", "denied"),
            subprocess_result(1, "", "denied"),
            subprocess_result(0, fake_profiler, ""),
        ]
        hw = get_hardware_info()
        check(hw["chip"] == "Apple M4 Max", f"system_profiler chip fallback: {hw['chip']}")
        check(hw["memory_gb"] == 128, f"system_profiler memory fallback: {hw['memory_gb']}")

    check(_extract_param_count("mlx-community/Qwen3-Next-80B-A3B-Instruct-6bit") == 80, "extract 80B model size")
    check(_extract_param_count("qwen2.5:14b-instruct-q4_K_M") == 14, "extract 14B model size")

    with mock.patch("pipeline.fit.detect_hf_cached_models") as mock_hf:
        mock_hf.return_value = [{
            "name": "mlx-community/Qwen2.5-14B-Instruct-4bit",
            "source": "huggingface",
            "size_gb": 8.0,
            "is_chat": True,
            "param_b": 14.0,
            "start_cmd": "mlx_lm.server --model mlx-community/Qwen2.5-14B-Instruct-4bit --port 8090",
        }]
        models = detect_local_models()
        check(len(models) == 1 and models[0]["source"] == "huggingface", "detect_local_models returns HF cached models")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write('{"mlx_url": "http://config-host:9000/v1/chat/completions"}\n')
        f.flush()
        probe_cfg = Path(f.name)

    endpoints = _build_models_endpoints(probe_cfg)
    check(endpoints[0] == "http://config-host:9000/v1/models", "configured endpoint probed first")
    check("http://localhost:8090/v1/models" in endpoints, "MLX default port included")
    probe_cfg.unlink()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("whisper_model: mlx-community/whisper-large-v3-mlx\nmlx_model: old-model\n# mlx_url: http://commented\n")
        f.flush()
        cfg = Path(f.name)

    check(
        read_config_value(cfg, "whisper_model") == "mlx-community/whisper-large-v3-mlx",
        "read existing key",
    )
    check(read_config_value(cfg, "nonexistent") == "", "read missing key returns empty")

    write_config_value(cfg, "mlx_model", "new-model")
    check(read_config_value(cfg, "mlx_model") == "new-model", "write replaces existing key")

    write_config_value(cfg, "mlx_url", "http://localhost:8090")
    check(read_config_value(cfg, "mlx_url") == "http://localhost:8090", "write uncomments and sets key")

    write_config_value(cfg, "new_key", "new_value")
    check(read_config_value(cfg, "new_key") == "new_value", "write appends missing key")

    tricky_value = '"pa|ss&word\\with spaces"'
    write_config_value(cfg, "publish.password", tricky_value)
    check(tricky_value in cfg.read_text(), "write preserves special characters in file content")
    check(read_config_value(cfg, "publish.password") == "pa|ss&word\\with spaces", "read strips outer quotes but preserves inner content")

    cfg.unlink()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("publish:\n  platform: local\n  password: \"\"\n")
        f.flush()
        nested_cfg = Path(f.name)

    write_config_value(nested_cfg, "publish.platform", "vercel")
    write_config_value(nested_cfg, "publish.password", '"secret"')
    nested_text = nested_cfg.read_text()
    check("  platform: vercel" in nested_text, "nested keys update in place")
    check("publish.platform:" not in nested_text, "nested key update does not append dotted top-level override")
    check("  password: \"secret\"" in nested_text, "nested password key updates in place")
    nested_cfg.unlink()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
