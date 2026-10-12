"""Create private relay credentials; never edits an existing MCP configuration."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

from wind_relay.common import IDENTIFIER, ROOT, digest, read_config, validate_base_url, write_private


def initialize(directory, url, clients, allow_private_http=False):
    directory = Path(directory).expanduser().absolute()
    url = validate_base_url(url, allow_private_http)
    if not clients or len(set(clients)) != len(clients) or any(not IDENTIFIER.fullmatch(x) for x in clients):
        raise ValueError("Use unique simple client IDs")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Output directory must be empty; existing credentials are never overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    worker_token = secrets.token_urlsafe(32)
    tokens = {name: secrets.token_urlsafe(32) for name in clients}
    write_private(directory / "gateway.json", {"database": "queue.sqlite",
                  "clients": {name: digest(token) for name, token in tokens.items()},
                  "worker_sha256": digest(worker_token)})
    write_private(directory / "worker.json", {"url": url, "token": worker_token,
                  "data_directory": "worker-state", "allow_private_http": allow_private_http})
    for name, token in tokens.items():
        write_private(directory / ("client-" + name + ".json"),
                      {"url": url, "token": token, "wait_seconds": 2, "allow_private_http": allow_private_http})
    return {"directory": str(directory), "clients": clients, "tokens_printed": False}


def client_config(path, python, format):
    config = Path(path).expanduser().absolute()
    read_config(config)  # Check that the private file exists; do not print its token.
    python = Path(python).expanduser().absolute()  # Preserve the venv symlink.
    entry = {"command": str(python), "args": [str(ROOT / "relay_client.py"), "--config", str(config)],
             "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}}
    if format == "workbuddy":
        return json.dumps({"mcpServers": {"wind_terminal_relay": entry}}, ensure_ascii=False, indent=2)
    lines = ["[mcp_servers.wind_terminal_relay]"]
    lines += [f"{key} = {json.dumps(entry[key], ensure_ascii=False)}" for key in ("command", "args")]
    lines += ["startup_timeout_sec = 30", "tool_timeout_sec = 30", "", "[mcp_servers.wind_terminal_relay.env]"]
    lines += [f"{key} = {json.dumps(value)}" for key, value in entry["env"].items()]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--output", default=".relay")
    init.add_argument("--url", default="http://127.0.0.1:8765")
    init.add_argument("--clients", nargs="+", default=["researcher1", "researcher2"])
    init.add_argument("--allow-private-http", action="store_true")
    client = sub.add_parser("client-config")
    client.add_argument("--config", required=True)
    client.add_argument("--python", default=sys.executable)
    client.add_argument("--format", choices=["codex", "workbuddy"], required=True)
    args = parser.parse_args()
    if args.action == "init":
        print(json.dumps(initialize(args.output, args.url, args.clients, args.allow_private_http), ensure_ascii=False))
    else:
        print(client_config(args.config, args.python, args.format))


if __name__ == "__main__":
    main()
