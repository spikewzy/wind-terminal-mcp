from __future__ import annotations

import json
import subprocess
import sys

from .common import ROOT, Problem, envelope, dumps
from .requests import validate
from .storage import Store
from .quota import local_quota_observation, quota_details
from .query_budget import QueryBudget
from .native_lock import native_lock
from .platform_support import inspect_platform, require_supported_platform, worker_environment


class Backend:
    def __init__(self, store: Store, runner=None, timeout=45):
        self.store = store
        self.runner = runner or self._native
        self.timeout = timeout
        self.budget = QueryBudget(store)

    def _attempt(self, method, arguments):
        reservation = self.budget.reserve(method, arguments)
        try:
            try:
                response = self.runner(method, arguments)
            except Problem as exc:
                from .common import failure
                response = failure(exc)
            receipt = self.store.save(method, arguments, response)
        except BaseException:
            self.budget.finish(reservation, state="failed_without_receipt")
            raise
        self.budget.finish(reservation, receipt["receipt_id"])
        receipt["local_request_budget"] = reservation
        return receipt

    def _native(self, method, arguments):
        from .native_support import require_native_method
        from .common import MODULE_DIR
        require_supported_platform()
        # Known missing functions do not need a native session or its lock.
        require_native_method(method)
        # Wind's native SDK is serialized across server instances sharing this profile.
        with native_lock(self.store.directory / "native.lock"):
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "wind_bridge.worker"],
                    cwd=ROOT, input=dumps({"method": method, "arguments": arguments}),
                    capture_output=True, text=True, encoding="utf-8", timeout=self.timeout,
                    env=worker_environment(MODULE_DIR),
                )
            except subprocess.TimeoutExpired:
                raise Problem("WIND_TIMEOUT", "WindPy query exceeded its time limit; worker was terminated") from None
        if result.returncode:
            raise Problem("WIND_WORKER_ERROR", "WindPy worker exited unexpectedly", exit_code=result.returncode)
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise Problem("WIND_PROTOCOL_ERROR", "Worker did not return one JSON response") from None
        return response

    def status(self, connect=False):
        if not connect:
            from .common import MODULE_DIR
            from .native_support import inspect_native_support
            return envelope(connected=None, connection_checked=False,
                            module_exists=bool(MODULE_DIR and (MODULE_DIR / "WindPy.py").is_file()),
                            module_directory=str(MODULE_DIR) if MODULE_DIR else None,
                            platform_support=inspect_platform(),
                            native_support=inspect_native_support(),
                            quota_observation=local_quota_observation(self.store),
                            local_request_budget=self.budget.inspect(),
                            message="Use connect=true for a live Wind API login check; this does not prove data entitlement.")
        receipt = self._attempt("__status__", {})
        response = receipt["response"]
        if not response.get("ok"):
            raise Problem(response.get("code", "WIND_CONNECTION_ERROR"), response.get("message", "Connection failed"), receipt_id=receipt["receipt_id"], detail=response)
        return envelope(**{k: v for k, v in response.items() if k != "ok"}, receipt_id=receipt["receipt_id"], connection_checked=True,
                        local_request_budget=self.budget.inspect(),
                        quota_observation=local_quota_observation(self.store))

    def request(self, method, arguments, max_age_seconds=0):
        args = validate(method, arguments)
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 0 <= max_age_seconds <= 86400:
            raise Problem("INVALID_PARAMS", "max_age_seconds must be 0..86400; 0 always queries Wind")
        receipt = self.store.cached(method, args, max_age_seconds) if max_age_seconds else None
        cached = receipt is not None
        if receipt is None:
            receipt = self._attempt(method, args)
        else:
            receipt["local_request_budget"] = {"native_request_reserved": False, "from_cache": True, "account_quota": False}
        response = receipt["response"]
        if not response.get("ok"):
            raise Problem(response.get("code", "WIND_RUNTIME_ERROR"), response.get("message", "WindPy failed"), receipt_id=receipt["receipt_id"], detail=response)
        raw = response["raw"]
        if raw.get("ErrorCode") != 0:
            quota = quota_details(raw, method)
            raise Problem("WIND_UPSTREAM_ERROR", "Wind terminal API returned an error; no alternate source was called",
                          wind_error_code=raw.get("ErrorCode"), wind_error_data=raw.get("Data"), receipt_id=receipt["receipt_id"],
                          **({"quota": quota, "error_category": quota["error_category"], "automatic_retry_recommended": False} if quota else {}))
        receipt["from_cache"] = cached
        return receipt
