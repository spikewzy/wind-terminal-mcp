from __future__ import annotations

import asyncio
import contextlib
import secrets
import time

from .common import RelayError, transport_error
from .http import Connection


class Proxy:
    def __init__(self, config, *, connection=None):
        self.connection = connection or Connection(config)
        self.wait_seconds = min(5, max(0, float(config.get("wait_seconds", 2))))

    @staticmethod
    def render(job):
        info = {"job_id": job["job_id"], "state": job["state"],
                "created_at": job["created_at"], "updated_at": job["updated_at"]}
        if job["state"] == "completed":
            return {**job["result"], "relay": {**job["result"].get("relay", {}), **info}}
        if job["state"] in {"queued", "running"}:
            return {"ok": True, "result_ready": False, "data_source_id": None, "relay": info,
                    "poll_tool": "relay_job_status", "next_poll_seconds": 2,
                    "do_not_resubmit": True, "message": "Job accepted; retrieve this same job ID until complete"}
        return transport_error("RELAY_JOB_" + job["state"].upper(),
                               "Job did not return data; do not automatically repeat a started query", **info)

    async def get(self, job_id, wait_seconds=None):
        seconds = self.wait_seconds if wait_seconds is None else min(5, max(0, float(wait_seconds)))
        deadline = time.monotonic() + seconds
        while True:
            job = await self.connection.request("GET", "/v1/jobs/" + job_id)
            if job["state"] not in {"queued", "running"} or time.monotonic() >= deadline:
                return self.render(job)
            await asyncio.sleep(min(0.3, max(0, deadline - time.monotonic())))

    async def call(self, name, arguments):
        job_id = secrets.token_hex(16)
        try:
            await self.connection.request("POST", "/v1/jobs", {"job_id": job_id, "tool": name, "arguments": arguments})
            return await self.get(job_id)
        except asyncio.CancelledError:
            # Queued cancellation cannot terminate a native query already running.
            with contextlib.suppress(RelayError):
                await self.connection.request("POST", f"/v1/jobs/{job_id}/cancel", {})
            raise
        except RelayError as exc:
            return transport_error(exc.code, str(exc), job_id=job_id,
                                   poll_tool="relay_job_status", do_not_resubmit=True)

    async def cancel(self, job_id):
        job = await self.connection.request("POST", f"/v1/jobs/{job_id}/cancel", {})
        if job["state"] == "cancelled":
            return {"ok": True, "cancelled": True, "data_source_id": None,
                    "relay": {"job_id": job["job_id"], "state": job["state"]}}
        return self.render(job)
