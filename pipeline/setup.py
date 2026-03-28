#!/usr/bin/env python3
"""Interactive minimal config setup."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich import ConfigError
from enrich import load_config
from fit import write_config_value


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python3 pipeline/setup.py <config> <example_config>")
        return 1

    config_path = Path(sys.argv[1])
    example_path = Path(sys.argv[2])

    print("Echobox Setup")
    print("=============")
    print("")

    if config_path.exists():
        print(f"Config already exists: {config_path}")
        print("Edit it directly, or delete it to re-run setup.")
        print("")
        print("Current settings:")
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            print(f"Current settings unavailable: {exc}")
            return 0
        for key in ("whisper_model", "mlx_model", "mlx_url", "publish.platform"):
            print(f"  {key}: {config.get(key, '(not set)')}")
        return 0

    shutil.copyfile(example_path, config_path)
    print("Created config from template.")
    print("")
    print("Minimum setup — answer these to get running:")
    print("")

    whisper_default = "mlx-community/whisper-large-v3-mlx"
    whisper_model = input(f"  Whisper model [{whisper_default}]: ").strip() or whisper_default
    mlx_url = "http://localhost:8090/v1/chat/completions"

    print("")
    use_vercel = input("  Publish reports to Vercel? [n]: ").strip().lower()
    publish_platform = "local"
    publish_password = ""
    if use_vercel in {"y", "yes"}:
        publish_platform = "vercel"
        publish_password = input("  Report password: ").strip()

    write_config_value(config_path, "whisper_model", whisper_model)
    write_config_value(config_path, "mlx_url", mlx_url)
    if publish_platform != "local":
        write_config_value(config_path, "publish.platform", publish_platform)
        if publish_password:
            write_config_value(config_path, "publish.password", f'"{publish_password}"')

    print("")
    print(f"Config saved: {config_path}")
    print("")
    print("Next steps:")
    print("  1. Run: ./echobox fit             (find best models)")
    print("  2. Start your MLX server")
    print("  3. Run: ./echobox watch           (start recording)")
    print("")
    print(f"  Edit config for more options: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
