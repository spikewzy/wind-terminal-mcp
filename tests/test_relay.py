"""Offline relay acceptance: authentication, isolation and no duplicate execution."""
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from relay_client import build_server
from relay_setup import client_config, initialize
from wind_relay.common import ROOT, RelayError, digest, load_contract, validate_base_url, write_private
from wind_relay.gateway import create_app
from wind_relay.http import Connection
from wind_relay.proxy import Proxy
from wind_relay.store import Queue
from wind_relay.worker import Worker

PARAMS = {"method": "edb", "arguments": {"codes": "S0033227", "beginTime": "2026-01-01",
           "endTime": "2026-01-04", "options": ""}}
PAYLOAD = {"tool": "query_wind_data", "arguments": PARAMS}
TOKEN_A, TOKEN_B, TOKEN_WORKER = "a" * 40, "b" * 40, "w" * 40


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.queue = Queue(Path(self.temp.name) / "queue.sqlite", clock=lambda: self.now, lease_seconds=3)

    def test_duplicate_submission_and_duplicate_completion_execute_once(self):
        job = "1" * 32
        self.queue.submit("a", job, PAYLOAD)
        self.queue.submit("a", job, PAYLOAD)
        claim = self.queue.claim("mock")
        self.assertEqual(claim["job_id"], job)
        self.assertIsNone(self.queue.claim("mock"))
        result = {"ok": True, "source": "fixture", "values": [None, 0, ""]}
        self.queue.complete(job, claim["lease_id"], result)
        self.queue.complete(job, claim["lease_id"], result)
        self.assertIsNone(self.queue.claim("mock"))
        self.assertEqual(self.queue.get("a", job)["result"], result)

    def test_started_lease_expiry_blocks_new_queries_without_replay(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        claim = self.queue.claim("native")
        self.queue.submit("b", "2" * 32, PAYLOAD)
        self.now += 4
        self.assertIsNone(self.queue.claim("native"))
        self.assertEqual(self.queue.get("a", "1" * 32)["state"], "uncertain")
        self.queue.complete(claim["job_id"], claim["lease_id"], {"ok": True})
        self.assertEqual(self.queue.claim("native")["job_id"], "2" * 32)

    def test_queued_cancel_never_claimed(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        self.queue.cancel("a", "1" * 32)
        self.assertIsNone(self.queue.claim("mock"))

    def test_other_client_cannot_get_cancel_or_reuse_job(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        for fn in [self.queue.get, self.queue.cancel]:
            with self.assertRaises(RelayError) as caught:
                fn("b", "1" * 32)
            self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(RelayError):
            self.queue.submit("b", "1" * 32, PAYLOAD)

    def test_different_payload_with_same_id_rejected(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        with self.assertRaises(RelayError):
            self.queue.submit("a", "1" * 32, {"tool": "wind_status", "arguments": {}})

    def test_retention_purges_result_but_does_not_allow_reexecution(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        claim = self.queue.claim("mock")
        self.queue.complete("1" * 32, claim["lease_id"], {"ok": True})
        self.now += 86401
        with self.assertRaises(RelayError) as caught:
            self.queue.submit("a", "1" * 32, PAYLOAD)
        self.assertEqual(caught.exception.code, "JOB_EXPIRED")
        self.assertIsNone(self.queue.claim("mock"))

    def test_queue_limit(self):
        for i in range(20):
            self.queue.submit("a", f"{i:032x}", PAYLOAD)
        with self.assertRaises(RelayError) as caught:
            self.queue.submit("a", "f" * 32, PAYLOAD)
        self.assertEqual(caught.exception.code, "QUEUE_FULL")

    def test_restart_keeps_pending_and_finished_jobs(self):
        self.queue.submit("a", "1" * 32, PAYLOAD)
        reopened = Queue(self.queue.path, clock=lambda: self.now)
        self.assertEqual(reopened.get("a", "1" * 32)["state"], "queued")


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = {"clients": {"a": digest(TOKEN_A), "b": digest(TOKEN_B)},
                       "worker_sha256": digest(TOKEN_WORKER), "database": str(self.directory / "queue.sqlite")}
        self.app = create_app(self.config)
        self.connections = []
        self.a, self.b, self.w = (self.connect(token) for token in (TOKEN_A, TOKEN_B, TOKEN_WORKER))

    def connect(self, token):
        connection = Connection({"url": "http://127.0.0.1", "token": token}, transport=httpx.ASGITransport(app=self.app))
        self.connections.append(connection)
        return connection

    async def asyncTearDown(self):
        for connection in self.connections:
            await connection.close()
        self.temp.cleanup()

    async def submit(self, connection=None, job="1" * 32):
        return await (connection or self.a).request("POST", "/v1/jobs", {"job_id": job, **PAYLOAD})

    async def test_authentication_and_role_separation(self):
        for connection, method, path, value in [
            (self.connect("z" * 40), "GET", "/v1/status", None),
            (self.a, "POST", "/v1/worker/claim", {}),
            (self.w, "GET", "/v1/status", None),
        ]:
            with self.assertRaises(RelayError) as caught:
                await connection.request(method, path, value)
            self.assertEqual(caught.exception.status, 401)

    async def test_http_result_isolation(self):
        await self.submit()
        with self.assertRaises(RelayError) as caught:
            await self.b.request("GET", "/v1/jobs/" + "1" * 32)
        self.assertEqual(caught.exception.status, 404)

    async def test_schema_and_tool_allowlist(self):
        for tool, arguments in [("eval", {}), ([], {}), ("query_wind_data", {})]:
            with self.assertRaises(RelayError) as caught:
                await self.a.request("POST", "/v1/jobs", {"job_id": "1" * 32, "tool": tool, "arguments": arguments})
            self.assertEqual(caught.exception.status, 400)
        self.assertIsNone(self.app.state.queue.claim("mock"))

    async def test_worker_source_mismatch_rejected(self):
        with self.assertRaises(RelayError) as caught:
            await self.w.request("POST", "/v1/worker/claim", {"mode": "mock", "server_sha256": "0" * 64})
        self.assertEqual(caught.exception.code, "CONTRACT_MISMATCH")

    async def test_mcp_protocol_lists_tools_and_returns_pending_id(self):
        proxy = Proxy({"wait_seconds": 0}, connection=self.a)
        async with create_connected_server_and_client_session(build_server(proxy)) as session:
            tools = await session.list_tools()
            self.assertEqual(len(tools.tools), 29)
            result = await session.call_tool("query_wind_data", PARAMS)
            self.assertFalse(result.isError)
            self.assertFalse(result.structuredContent["result_ready"])
            self.assertEqual(result.structuredContent["poll_tool"], "relay_job_status")
            job_id = result.structuredContent["relay"]["job_id"]
            cancelled = await session.call_tool("relay_cancel_job", {"job_id": job_id})
            self.assertFalse(cancelled.isError)
            self.assertTrue(cancelled.structuredContent["cancelled"])
            self.assertEqual(cancelled.structuredContent["relay"]["state"], "cancelled")

    async def test_transport_error_keeps_saved_receipt_after_job_completion(self):
        await self.submit()
        claimed = self.app.state.queue.claim("mock")
        self.app.state.queue.complete(claimed["job_id"], claimed["lease_id"],
                                     {"ok": False, "code": "RESULT_TOO_LARGE",
                                      "relay": {"receipt_id": "saved-receipt"}})
        result = await Proxy({"wait_seconds": 0}, connection=self.a).get("1" * 32)
        self.assertEqual(result["relay"]["receipt_id"], "saved-receipt")
        self.assertEqual(result["relay"]["state"], "completed")

    async def test_slow_execution_does_not_block_status_or_other_clients(self):
        await self.submit()
        started, release = __import__("threading").Event(), __import__("threading").Event()
        def execute(job):
            started.set()
            release.wait(3)
            return {"ok": True, "data_source_id": "relay_mock", "raw": {"Data": [[None, 0, ""]]}}
        worker = Worker({"data_directory": str(self.directory)}, execute, connection=self.w)
        task = asyncio.create_task(worker.step())
        await asyncio.to_thread(started.wait, 2)
        status = await asyncio.wait_for(self.b.request("GET", "/v1/status"), 1)
        self.assertTrue(status["worker_online"])
        self.assertEqual(status["own_pending_jobs"], 0)
        await self.submit(self.b, "2" * 32)
        release.set()
        self.assertTrue(await task)
        result = await self.a.request("GET", "/v1/jobs/" + "1" * 32)
        self.assertEqual(result["result"]["raw"]["Data"], [[None, 0, ""]])

    async def test_lost_completion_ack_retries_delivery_not_execution(self):
        await self.submit()
        calls = []
        def execute(job):
            calls.append(job["job_id"])
            return {"ok": True, "data_source_id": "relay_mock", "source": "synthetic"}
        class LostAck:
            lost = False
            async def request(inner, method, path, payload=None):
                result = await self.w.request(method, path, payload)
                if path.endswith("/complete") and not inner.lost:
                    inner.lost = True
                    raise RelayError("RELAY_UNREACHABLE", "simulated lost acknowledgement", 503)
                return result
        worker = Worker({"data_directory": str(self.directory)}, execute, connection=LostAck())
        with self.assertRaises(RelayError):
            await worker.step()
        self.assertTrue(worker.inflight.exists())
        restarted = Worker({"data_directory": str(self.directory)}, execute, connection=self.w)
        self.assertFalse(await restarted.step())
        self.assertEqual(calls, ["1" * 32])
        self.assertFalse(restarted.inflight.exists())

    async def test_started_crash_marker_requires_manual_inspection(self):
        worker = Worker({"data_directory": str(self.directory)}, lambda job: self.fail("Must not execute"), connection=self.w)
        write_private(worker.inflight, {"job_id": "1" * 32, "lease_id": "l"})
        with self.assertRaises(RelayError) as caught:
            await worker.step()
        self.assertEqual(caught.exception.code, "STARTED_JOB_UNCERTAIN")

    async def test_quota_error_halts_worker_before_next_query(self):
        await self.submit()
        await self.submit(self.b, "2" * 32)
        calls = []
        def execute(job):
            calls.append(job["job_id"])
            return {"ok": False, "error_category": "quota_exceeded"}
        worker = Worker({"data_directory": str(self.directory)}, execute, connection=self.w)
        for _ in range(2):
            with self.assertRaises(RelayError) as caught:
                await worker.step()
            self.assertEqual(caught.exception.code, "WORKER_HALTED")
        self.assertEqual(calls, ["1" * 32])
        self.assertEqual(self.app.state.queue.get("b", "2" * 32)["state"], "queued")

    async def test_successful_metadata_and_saved_errors_do_not_halt_worker(self):
        # This is the historical evidence shipped in wind_capabilities, not a
        # quota error from the current user's request.
        historical = json.loads((ROOT / "references/data-coverage.json").read_text())["quota_observation"]
        self.assertEqual(historical["error_category"], "quota_exceeded")
        fixtures = [
            ("wind_capabilities", {}, {"data_coverage": {"quota_observation": historical}}),
            ("wind_status", {"connect": False}, {"ok": True, "connection_checked": False,
             "quota_observation": {"last_observed_error": historical}}),
            ("read_wind_receipt", {"receipt_id": "9" * 32}, {"ok": True,
             "receipt": {"response": {"ok": False, "error_category": "quota_exceeded"}}}),
        ]
        for index, (tool, arguments, result) in enumerate(fixtures):
            with self.subTest(tool=tool):
                first, second = f"{index * 2 + 10:032x}", f"{index * 2 + 11:032x}"
                await self.a.request("POST", "/v1/jobs", {
                    "job_id": first, "tool": tool, "arguments": arguments})
                await self.submit(self.b, second)
                calls = []
                def execute(job):
                    calls.append(job["job_id"])
                    return result if job["job_id"] == first else {"ok": True, "data_source_id": "relay_mock"}
                worker = Worker({"data_directory": str(self.directory / tool)}, execute, connection=self.w)
                self.assertTrue(await worker.step())
                self.assertFalse(worker.halt.exists())
                self.assertTrue(await worker.step())
                self.assertEqual(calls, [first, second])
                self.assertEqual(self.app.state.queue.get("b", second)["state"], "completed")

    async def test_current_nested_batch_quota_failure_still_halts(self):
        await self.submit()
        await self.submit(self.b, "2" * 32)
        calls = []
        def execute(job):
            calls.append(job["job_id"])
            return {"ok": False, "derived": {"status": "batch_failed", "error": {
                "code": "WIND_UPSTREAM_ERROR", "error_category": "quota_exceeded"}}}
        worker = Worker({"data_directory": str(self.directory)}, execute, connection=self.w)
        with self.assertRaises(RelayError) as caught:
            await worker.step()
        self.assertEqual(caught.exception.code, "WORKER_HALTED")
        self.assertTrue(worker.halt.exists())
        self.assertEqual(calls, ["1" * 32])
        self.assertEqual(self.app.state.queue.get("b", "2" * 32)["state"], "queued")


class ContractTests(unittest.TestCase):
    def test_existing_profile_lock_and_budget_cover_all_relay_clients(self):
        from types import SimpleNamespace
        from wind_bridge.common import Problem
        from wind_bridge.native_lock import native_lock as actual_lock
        from wind_relay.executor import ToolExecutor

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            root = Path(directory)
            executor = ToolExecutor({"data_directory": str(root / "data"),
                                     "shared_profile": str(root / "existing-profile")}, "native")
            (executor.shared / "query-budget.json").write_text('{"max_native_calls":2}')
            fixture = json.dumps(ToolExecutor.fake_native("edb", PARAMS["arguments"]))
            with patch("wind_bridge.native_lock.native_lock", side_effect=lambda path: actual_lock(path, timeout=0.01)), \
                 patch("wind_bridge.backend.require_supported_platform"), \
                 patch("wind_bridge.backend.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=fixture)) as sdk:
                a, b = executor.service("a").backend, executor.service("b").backend
                with actual_lock(executor.shared / "native.lock"):
                    with self.assertRaises(Problem) as busy:
                        a.request("edb", PARAMS["arguments"])
                    self.assertEqual(busy.exception.code, "WIND_BUSY")
                    sdk.assert_not_called()
                self.assertTrue(b.request("edb", PARAMS["arguments"])["response"]["ok"])
                with self.assertRaises(Problem) as budget:
                    a.request("edb", PARAMS["arguments"])
                self.assertEqual(budget.exception.code, "LOCAL_BUDGET_EXCEEDED")
                self.assertEqual(sdk.call_count, 1)
                self.assertEqual(executor.budget.inspect()["usage"]["reserved_native_calls"], 2)

    def test_contract_matches_current_tool_definitions_without_importing_server(self):
        contract = load_contract()
        source = (ROOT / "server.py").read_bytes()
        self.assertEqual(contract["server_sha256"], hashlib.sha256(source).hexdigest())
        tree = ast.parse(source)
        names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                 and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "tool" for d in node.decorator_list)}
        self.assertEqual(names, {x["name"] for x in contract["tools"]})

    def test_remote_http_requires_explicit_private_tunnel(self):
        with self.assertRaises(RelayError):
            validate_base_url("http://203.0.113.10:8765")
        self.assertEqual(validate_base_url("http://10.1.1.1:8765", True), "http://10.1.1.1:8765")
        for url in ["https://user:password@example.com", "https://example.com?token=x"]:
            with self.assertRaises(RelayError):
                validate_base_url(url)

    def test_generated_configs_do_not_print_tokens_or_overwrite_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private"
            report = initialize(private, "http://127.0.0.1:8765", ["a", "b"])
            self.assertFalse(report["tokens_printed"])
            a = json.loads((private / "client-a.json").read_text())
            gateway = json.loads((private / "gateway.json").read_text())
            self.assertEqual(gateway["clients"]["a"], digest(a["token"]))
            import tomllib
            text = client_config(private / "client-a.json", ROOT / ".venv/bin/python", "codex")
            self.assertNotIn(a["token"], text)
            parsed = tomllib.loads(text)
            self.assertIn("wind_terminal_relay", parsed["mcp_servers"])
            with self.assertRaises(ValueError):
                initialize(private, a["url"], ["a"])


if __name__ == "__main__":
    unittest.main()
