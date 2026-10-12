"""Outbound execution host. Mock is the default; native must be explicit."""
import argparse
import asyncio
import logging
from pathlib import Path

from wind_relay.common import read_config
from wind_relay.executor import ToolExecutor
from wind_relay.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["mock", "native"], default="mock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = read_config(args.config)
    executor = ToolExecutor(config, args.mode)
    # Importing tool definitions is not starting an MCP transport or WindPy.
    # The OS-owned worker lock prevents two relay instances on this profile.
    from wind_bridge.native_lock import native_lock
    directory = Path(config["data_directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with native_lock(directory / "relay-worker.lock", timeout=0):
        asyncio.run(Worker(config, executor, args.mode).run())


if __name__ == "__main__":
    main()
