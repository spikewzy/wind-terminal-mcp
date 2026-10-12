"""Reuse existing tool bodies with a separate data profile for each client."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

from .common import IDENTIFIER, ROOT, RelayError, load_contract


class ToolExecutor:
    def __init__(self, config, mode="mock"):
        if mode not in {"mock", "native"}:
            raise ValueError("Unknown execution mode")
        self.mode = mode
        self.root = Path(config.get("mcp_root", ROOT)).resolve()
        self.data = Path(config["data_directory"]).resolve() / mode
        self.data.mkdir(parents=True, exist_ok=True)
        contract = load_contract()
        if hashlib.sha256((self.root / "server.py").read_bytes()).hexdigest() != contract["server_sha256"]:
            raise RelayError("CONTRACT_MISMATCH", "Execution host server.py differs from the shipped tool contract")
        self.names = {tool["name"] for tool in contract["tools"]}
        shared = Path(config.get("shared_profile", os.environ.get("WIND_TERMINAL_MCP_DATA_DIR", self.root / "runtime"))).resolve()
        # Mock execution never writes to a supplied production profile.
        if mode == "mock":
            shared = self.data / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        os.environ["WIND_TERMINAL_MCP_DATA_DIR"] = str(shared)
        sys.path.insert(0, str(self.root))
        spec = importlib.util.spec_from_file_location("_wind_relay_host_tools", self.root / "server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.module, self.shared = module, shared
        from wind_bridge.query_budget import QueryBudget
        from wind_bridge.storage import Store
        self.budget = QueryBudget(Store(shared))
        self.services = {}

    @staticmethod
    def fake_native(method, arguments):
        if method == "__status__":
            return {"ok": True, "connected": False, "message": "Synthetic fixture; Wind login was not checked"}
        if method not in {"edb", "wsd", "wss", "wsq"}:
            return {"ok": False, "code": "MOCK_METHOD_UNSUPPORTED", "message": "Mock mode supports edb/wsd/wss/wsq only"}
        codes = arguments["codes"].split(",")
        fields = ["CLOSE"] if method == "edb" else [x.upper() for x in arguments["fields"].split(",")]
        if method in {"edb", "wsd"}:
            start, end = (dt.date.fromisoformat(arguments[key][:10]) for key in ("beginTime", "endTime"))
            times = [(start + dt.timedelta(days=i)).isoformat() for i in range(min(4, (end - start).days + 1))]
            columns = len(codes) if method == "edb" or len(codes) > 1 else len(fields)
            data = [[1.0, None, 0.0, ""][:len(times)] for _ in range(columns)]
        else:
            times = []
            data = [[0.0 for _ in codes] for _ in fields]
        return {"ok": True, "raw": {"ErrorCode": 0, "Codes": codes, "Fields": fields,
                                    "Times": times, "Data": data}}

    def service(self, owner):
        if not IDENTIFIER.fullmatch(owner):
            raise RelayError("INVALID_CLIENT", "Invalid client ID")
        if owner not in self.services:
            from wind_bridge.backend import Backend
            from wind_bridge.native_lock import native_lock
            from wind_bridge.service import Service
            from wind_bridge.storage import Store
            shared = self.shared

            class RelayBackend(Backend):
                def _native(self, method, arguments):
                    # Also coordinate with an existing ordinary local MCP using
                    # this exact shared profile. Per-user receipts remain separate.
                    with native_lock(shared / "native.lock"):
                        return super()._native(method, arguments)

            store = Store(self.data / "clients" / owner)
            backend = RelayBackend(store, runner=self.fake_native if self.mode == "mock" else None)
            backend.budget = self.budget
            self.services[owner] = Service(store, backend)
        return self.services[owner]

    def __call__(self, job):
        if job["tool"] not in self.names:
            raise RelayError("UNKNOWN_TOOL", "Tool is not in the contract")
        self.module.service = self.service(job["client_id"])
        result = getattr(self.module, job["tool"])(**job["arguments"])
        if self.mode == "mock":
            def mark(value):
                if isinstance(value, dict):
                    marked = {k: mark(v) for k, v in value.items()}
                    if marked.get("data_source_id") == "wind_terminal_api":
                        marked.update(data_source_id="relay_mock", data_source_name="Synthetic relay fixture")
                    return marked
                if isinstance(value, list):
                    return [mark(v) for v in value]
                return value
            result = mark(result)
            result.update(data_source_id="relay_mock", data_source_name="Synthetic relay fixture")
            result["synthetic_fixture"] = True
            result["live_wind_query"] = False
        result["relay_execution"] = {"mode": self.mode, "client_profile_isolated": True}
        return result
