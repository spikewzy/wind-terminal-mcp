"""Print a local MCP client configuration; never edit client settings or log in."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from wind_bridge.platform_support import discover_module_dir, venv_python

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "codex", "workbuddy"), default="json")
    parser.add_argument("--python", type=Path, default=venv_python(ROOT / ".venv"))
    parser.add_argument("--wind-module-dir", type=Path)
    parser.add_argument("--wind-library-dir", type=Path)
    args = parser.parse_args()
    # Preserve the virtualenv path: resolving its symlink would select the base Python.
    python = args.python.expanduser().absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        parser.error(f"Python executable not found: {python}")
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    module_dir = args.wind_module_dir.expanduser().absolute() if args.wind_module_dir else discover_module_dir()
    if module_dir is not None:
        if args.wind_module_dir and not (module_dir / "WindPy.py").is_file():
            parser.error("--wind-module-dir must contain the official WindPy.py")
        env["WIND_TERMINAL_MODULE_DIR"] = str(module_dir)
    library_dir = args.wind_library_dir or os.environ.get("WIND_TERMINAL_LIBRARY_DIR")
    if library_dir:
        path = Path(library_dir).expanduser().absolute()
        if not path.is_dir():
            parser.error("Wind library directory does not exist")
        env["WIND_TERMINAL_LIBRARY_DIR"] = str(path)
    entry = {"command": str(python), "args": [str(ROOT / "server.py")], "env": env}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.format in {"json", "workbuddy"}:
        # WorkBuddy documents local servers using command/args without a type key.
        server = entry if args.format == "workbuddy" else {"type": "stdio", **entry}
        print(json.dumps({"mcpServers": {"wind_terminal_api": server}},
                         ensure_ascii=False, indent=2))
    else:
        print("[mcp_servers.wind_terminal_api]")
        for key, value in entry.items():
            if key == "env":
                continue
            print(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        print("startup_timeout_sec = 30")
        print("tool_timeout_sec = 180")
        print("\n[mcp_servers.wind_terminal_api.env]")
        for key, value in env.items():
            print(f"{key} = {json.dumps(value, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
