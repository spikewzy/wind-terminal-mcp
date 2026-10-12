"""Run the complete synthetic relay; real WindPy imports are forbidden."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import importlib.abc
import json
import logging
from pathlib import Path
import socket
import sys
import tempfile
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
import uvicorn

from relay_client import build_server
from relay_setup import initialize
from wind_relay.common import ROOT, read_config, read_json
from wind_relay.executor import ToolExecutor
from wind_relay.gateway import create_app
from wind_relay.http import Connection
from wind_relay.proxy import Proxy
from wind_relay.worker import Worker


class ForbidWindPy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "WindPy" or fullname.startswith("WindPy."):
            raise RuntimeError("Real WindPy is forbidden in this mock relay demo")


async def complete(session, value):
    result = value.structuredContent
    deadline = time.monotonic() + 15
    while result.get("result_ready") is False:
        if not result.get("ok") or time.monotonic() >= deadline:
            raise AssertionError("Mock job did not finish")
        await asyncio.sleep(0.05)
        value = await session.call_tool("relay_job_status", {"job_id": result["relay"]["job_id"], "wait_seconds": 0})
        result = value.structuredContent
    return result


async def exercise(directory, network):
    checks = []
    server_task, gateway_server = None, None
    if network:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
    else:
        port = 8765
    initialize(directory / "private", f"http://127.0.0.1:{port}", ["a", "b"])
    private = directory / "private"
    app = create_app(read_config(private / "gateway.json"))
    if network:
        gateway_server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        server_task = asyncio.create_task(gateway_server.serve(sockets=[sock]))
        for _ in range(100):
            if gateway_server.started:
                break
            await asyncio.sleep(0.02)
        if not gateway_server.started:
            raise AssertionError("Loopback gateway failed to start")
    transport = None if network else httpx.ASGITransport(app=app)
    worker_config = read_config(private / "worker.json")
    executor = ToolExecutor(worker_config, "mock")
    calls = []
    def execute(job):
        calls.append(job["tool"])
        return executor(job)
    connection = Connection(worker_config, transport=transport)
    worker = Worker(worker_config, execute, connection=connection)
    stop = asyncio.Event()
    async def worker_loop():
        while not stop.is_set():
            await worker.step()
            await asyncio.sleep(0.01)
    worker_task = asyncio.create_task(worker_loop())
    connections = []
    try:
        async with contextlib.AsyncExitStack() as stack:
            sessions = []
            for owner in ["a", "b"]:
                client_path = private / ("client-" + owner + ".json")
                config = read_json(client_path)
                config["wait_seconds"] = 0
                client_path.write_text(json.dumps(config), encoding="utf-8")
                if network:
                    # Child process is only the relay proxy, never server.py.
                    params = StdioServerParameters(command=sys.executable,
                            args=[str(ROOT / "relay_client.py"), "--config", str(client_path)],
                            env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                else:
                    connection = Connection(config, transport=transport)
                    connections.append(connection)
                    session = await stack.enter_async_context(create_connected_server_and_client_session(
                              build_server(Proxy(config, connection=connection))))
                sessions.append(session)
            a, b = sessions
            assert len((await a.list_tools()).tools) == 29
            checks.append("29_tools_discovered_over_mcp")
            arguments = {"method": "edb", "arguments": {"codes": "S0033227", "beginTime": "2026-01-01",
                         "endTime": "2026-01-04", "options": ""}}
            before = time.monotonic()
            pending = await a.call_tool("query_wind_data", arguments)
            duration = time.monotonic() - before
            first = await complete(a, pending)
            assert first["ok"] and first["data_source_id"] == "relay_mock"
            assert first["synthetic_fixture"] and first["live_wind_query"] is False
            assert first["arguments"] == arguments["arguments"]
            assert first["raw"]["Data"] == [[1.0, None, 0.0, ""]]
            checks.extend(["synthetic_source_explicit", "original_arguments_preserved", "null_zero_empty_preserved"])
            own = await complete(a, await a.call_tool("read_wind_receipt", {"receipt_id": first["receipt_id"]}))
            assert own["ok"]
            denied = await complete(b, await b.call_tool("read_wind_receipt", {"receipt_id": first["receipt_id"]}))
            assert not denied["ok"] and denied["code"] == "NO_RECEIPT"
            checks.append("receipt_isolated_between_clients")
            second = await complete(b, await b.call_tool("query_wind_data", arguments))
            assert second["ok"] and second["receipt_id"] != first["receipt_id"]
            assert calls.count("query_wind_data") == 2
            checks.append("two_clients_use_one_serial_execution_worker")
            status = (await a.call_tool("relay_status", {})).structuredContent
            assert status["worker_online"] and status["worker_mode"] == "mock"
            assert "WindPy" not in sys.modules
            checks.append("real_windpy_not_imported")
            return {"ok": True, "mode": "mock", "loopback_http": network, "stdio_mcp": network,
                    "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "checks": checks, "tool_body_executions": len(calls),
                    "first_mcp_return_seconds": round(duration, 4), "WindPy_imported": False,
                    "real_wind_queries": 0, "cloud_or_uos_deployed": False}
    finally:
        stop.set()
        await worker_task
        await worker.connection.close()
        for connection in connections:
            await connection.close()
        if gateway_server:
            gateway_server.should_exit = True
            await server_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="Also test actual loopback TCP HTTP and two stdio proxy processes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    sys.meta_path.insert(0, ForbidWindPy())
    with tempfile.TemporaryDirectory(prefix="wind-relay-mock-") as directory:
        result = asyncio.run(exercise(Path(directory), args.network))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
