"""Interactive live execution without putting credentials in files or command history."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from lingban_pipeline.cli import main
from lingban_pipeline.io import load, safe_config


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--upstream-result")
    args = parser.parse_args()
    from pathlib import Path
    config = load(Path(args.config))
    safe_config(config)
    names = set()
    if config.get("text_model", {}).get("enabled"):
        names.add(config["text_model"].get("credential_env", "DEEPSEEK_API_KEY"))
    for branch in ("tts", "sfx"):
        provider = config.get(branch, {})
        if provider.get("adapter") not in {None, "local", "compose", "library"}:
            names.add(provider.get("credential_env", "DASHSCOPE_API_KEY"))
    missing = [name for name in sorted(names) if not os.environ.get(name)]
    if missing and not sys.stdin.isatty():
        raise SystemExit("Run in an interactive terminal for hidden credential input, or supply process environment variables.")
    inserted = []
    argv = sys.argv
    try:
        for name in missing:
            value = getpass.getpass(f"{name} (hidden, current process only): ")
            if not value.strip():
                raise SystemExit("Credential input is empty; no model request submitted.")
            os.environ[name] = value
            inserted.append(name)
        sys.argv = ["lingban", "run", "--input", args.input, "--config", args.config, "--root", args.root, "--thread", args.thread]
        if args.upstream_result:
            sys.argv.extend(["--upstream-result", args.upstream_result])
        main()
    finally:
        sys.argv = argv
        for name in inserted:
            os.environ.pop(name, None)


if __name__ == "__main__":
    run()
