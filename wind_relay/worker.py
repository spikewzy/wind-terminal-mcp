from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from .common import MAX_RESULT_BYTES, RelayError, encode, load_contract, read_json, transport_error, write_private
from .http import Connection

logger = logging.getLogger("wind_relay.worker")


class Worker:
    def __init__(self, config, executor, mode="mock", *, connection=None):
        self.mode, self.executor = mode, executor
        self.connection = connection or Connection(config)
        self.directory = Path(config["data_directory"]).resolve() / mode
        self.outbox = self.directory / "outbox"
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.inflight = self.directory / "inflight.json"
        self.halt = self.directory / "halt.json"
        self.contract = load_contract()

    async def flush(self):
        for path in sorted(self.outbox.glob("*.json")):
            item = read_json(path)
            await self.connection.request("POST", f"/v1/worker/{item['job_id']}/complete",
                                          {"lease_id": item["lease_id"], "result": item["result"]})
            path.unlink()
            if self.inflight.exists() and read_json(self.inflight)["job_id"] == item["job_id"]:
                self.inflight.unlink()

    async def heartbeat(self, job):
        interval = max(1, min(10, job["lease_seconds"] / 3))
        while True:
            try:
                await self.connection.request("POST", f"/v1/worker/{job['job_id']}/heartbeat",
                                              {"lease_id": job["lease_id"]})
            except RelayError as exc:
                logger.warning("heartbeat failed code=%s; started job will not be replayed", exc.code)
            await asyncio.sleep(interval)

    async def step(self):
        await self.flush()
        if self.halt.exists():
            raise RelayError("WORKER_HALTED", "A Wind quota error paused this worker. Inspect halt.json and verify quota recovery before manually clearing it", 409)
        if self.inflight.exists():
            raise RelayError("STARTED_JOB_UNCERTAIN", "A previous started job has no saved result. Inspect the execution host before resolving; it will not be replayed", 409)
        claimed = await self.connection.request("POST", "/v1/worker/claim",
                                               {"mode": self.mode, "server_sha256": self.contract["server_sha256"]})
        job = claimed["job"]
        if job is None:
            return False
        write_private(self.inflight, {"job_id": job["job_id"], "lease_id": job["lease_id"]})
        heart = asyncio.create_task(self.heartbeat(job))
        try:
            try:
                result = await asyncio.to_thread(self.executor, job)
                if len(encode(result)) > MAX_RESULT_BYTES - 4096:
                    result = transport_error("RESULT_TOO_LARGE", "Result is saved on the execution host; use a bounded query or receipt page",
                                             receipt_id=result.get("receipt_id"), job_id=job["job_id"])
            except Exception as exc:
                logger.error("executor failed type=%s", type(exc).__name__)
                result = transport_error("EXECUTION_ERROR", "Execution host failed: " + type(exc).__name__)
            def quota_exceeded(value):
                if isinstance(value, dict):
                    return value.get("error_category") == "quota_exceeded" or any(quota_exceeded(v) for v in value.values())
                if isinstance(value, list):
                    return any(quota_exceeded(v) for v in value)
                return False
            if quota_exceeded(result):
                write_private(self.halt, {"code": "WIND_QUOTA_EXCEEDED", "job_id": job["job_id"],
                                         "automatic_retry": False})
            write_private(self.outbox / (job["job_id"] + ".json"),
                          {"job_id": job["job_id"], "lease_id": job["lease_id"], "result": result})
            await self.flush()
            if self.halt.exists():
                raise RelayError("WORKER_HALTED", "Wind quota error saved; new jobs are paused", 409)
            return True
        finally:
            heart.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heart

    async def run(self):
        try:
            while True:
                try:
                    worked = await self.step()
                except RelayError as exc:
                    if exc.code not in {"RELAY_UNREACHABLE", "RELAY_PROTOCOL_ERROR"} and exc.status < 500:
                        raise
                    logger.warning("relay unavailable code=%s; retrying delivery only", exc.code)
                    await asyncio.sleep(3)
                else:
                    if not worked:
                        await asyncio.sleep(1)
        finally:
            await self.connection.close()
