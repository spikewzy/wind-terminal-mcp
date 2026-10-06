"""Install the desktop MCP on macOS, Windows or the supported Xinchuang editions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

from wind_bridge.platform_support import discover_module_dir, inspect_platform, support_policy, venv_python

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, help="Install offline from a directory of downloaded wheels")
    parser.add_argument("--wind-module-dir", type=Path, help="Directory containing the official platform-specific WindPy.py")
    parser.add_argument("--wind-library-dir", type=Path, help="Optional directory containing the official native Wind libraries")
    parser.add_argument("--check", action="store_true", help="Inspect this OS/Python/SDK without changing files or querying Wind")
    args = parser.parse_args()
    supported = inspect_platform()
    module_dir = args.wind_module_dir.expanduser().absolute() if args.wind_module_dir else discover_module_dir()
    if args.check:
        print(json.dumps({"platform": supported, "policy": support_policy(),
                          "python_version": platform.python_version(),
                          "module_directory": str(module_dir) if module_dir else None,
                          "module_exists": bool(module_dir and (module_dir / "WindPy.py").is_file()),
                          "native_login_tested": False}, ensure_ascii=False))
        return 0 if supported["supported"] and sys.version_info >= (3, 10) else 2
    if not supported["supported"]:
        parser.error(f"Unsupported OS: {supported['reason']}. Only macOS, Windows, NFS Desktop 5.0, UOS Desktop 20 and Kylin Desktop V10 SP1; no other Linux or Linux server editions.")
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required; Python 3.12 is verified with the local Wind SDK.")
    environment = ROOT / ".venv"
    if environment.exists() and not (environment / "pyvenv.cfg").is_file():
        parser.error(".venv already exists but is not a virtual environment; it was left unchanged.")
    if args.wheelhouse and not args.wheelhouse.expanduser().is_dir():
        parser.error("The supplied wheelhouse directory does not exist.")
    if args.wind_module_dir and not (module_dir / "WindPy.py").is_file():
        parser.error("--wind-module-dir must contain the official WindPy.py; no SDK is downloaded by this installer.")
    if args.wind_library_dir and not args.wind_library_dir.expanduser().is_dir():
        parser.error("--wind-library-dir must be an existing directory.")
    if not environment.exists():
        print("Creating the project Python environment.", flush=True)
        venv.EnvBuilder(with_pip=True, symlinks=platform.system() != "Windows").create(environment)
    python = venv_python(environment)
    if not python.is_file():
        parser.error("The existing .venv does not contain this platform's Python executable; it was left unchanged. Install into a fresh directory.")
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
               "-r", str(ROOT / "requirements.lock.txt")]
    if args.wheelhouse:
        command += ["--no-index", "--find-links", str(args.wheelhouse.expanduser().resolve())]
    subprocess.run(command, check=True, cwd=ROOT)
    subprocess.run([str(python), "-m", "pip", "check"], check=True, cwd=ROOT)
    configs = ROOT / "client-configs"
    configs.mkdir(exist_ok=True)
    for fmt, filename in (("json", "mcp.json"), ("codex", "codex.toml"),
                          ("workbuddy", "workbuddy.json")):
        command = [str(python), str(ROOT / "client_config.py"), "--format", fmt]
        if module_dir and (module_dir / "WindPy.py").is_file():
            command += ["--wind-module-dir", str(module_dir)]
        if args.wind_library_dir:
            command += ["--wind-library-dir", str(args.wind_library_dir.expanduser().absolute())]
        result = subprocess.run(command, check=True, text=True, encoding="utf-8", capture_output=True, cwd=ROOT,
                                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        (configs / filename).write_text(result.stdout, encoding="utf-8")
    print(json.dumps({"installed": True, "directory": str(ROOT), "configs": str(configs),
                      "client_settings_modified": False, "wind_login_tested": False,
                      "platform": supported, "module_directory": str(module_dir) if module_dir else None,
                      "next_step": [str(python), "verify_delivery.py"],
                      "live_check_optional": "Add --live only when a small Wind data query is intended."}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
