"""Local preflight budgets shared by MCP processes using the same data profile.

Reservations are conservative attempt counts, never Wind account billing units.
They survive timeouts/crashes and are not refunded by failed upstream requests.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid

from .common import Problem
from .requests import csv


DEFAULT_POLICY = {
    "schema_version": 1, "window_seconds": 7 * 24 * 3600,
    "max_native_calls": None, "max_estimated_cells": None,
    "max_estimated_cells_per_request": None,
}
LIMITS = ("max_native_calls", "max_estimated_cells", "max_estimated_cells_per_request")


def estimate_cells(method, arguments):
    """Estimate the valid output grid, not billed usage or real trading dates."""
    result = {"estimated_cells": None, "model": "unknown", "billing_estimate": False}
    if method == "__status__":
        return {**result, "estimated_cells": 0, "model": "connection_check_no_data_grid"}
    if method in {"wss", "wsee", "wsq"}:
        count = len(csv(arguments["codes"])) * len(csv(arguments["fields"], "fields"))
        return {**result, "estimated_cells": count, "model": "requested_code_field_grid"}
    if method in {"wsd", "wses", "edb", "tdays"}:
        try:
            start, end = (dt.date.fromisoformat(arguments[key]) for key in ("beginTime", "endTime"))
        except (ValueError, TypeError, KeyError):
            return result
        # Unsupported or ambiguous frequency syntax must not look cheap.
        options = {}
        for item in arguments.get("options", "").split(";"):
            if not item.strip():
                continue
            if item.count("=") != 1:
                return result
            key, value = (part.strip().casefold() for part in item.split("="))
            if not key or key in options:
                return result
            options[key] = value
        if options.get("period", "d") not in {"d", "w", "m", "q", "s", "y"} or "barsize" in options:
            return result
        days = (end - start).days + 1
        if days <= 0:
            return result
        codes = 1 if method == "tdays" else len(csv(arguments["codes"]))
        fields = 1 if method in {"tdays", "edb"} else len(csv(arguments["fields"], "fields"))
        return {**result, "estimated_cells": days * codes * fields,
                "model": "calendar_day_grid_including_nontrading_days",
                "calendar_days": days, "trading_calendar_queried": False}
    if method in {"tdaysoffset", "tdayscount"}:
        return {**result, "estimated_cells": 1, "model": "single_calendar_result"}
    return result


class QueryBudget:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        self.path = store.directory / "query-budget.json"
        with store.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS query_budget_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1), tracking_started_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS query_budget_attempts (
                    id TEXT PRIMARY KEY, reserved_at REAL NOT NULL, method TEXT NOT NULL,
                    estimated_cells INTEGER, state TEXT NOT NULL, receipt_id TEXT
                );
                CREATE INDEX IF NOT EXISTS query_budget_window ON query_budget_attempts(reserved_at);
            ''')
            con.execute("INSERT OR IGNORE INTO query_budget_meta VALUES(1,?)", (self.clock(),))

    def policy(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(DEFAULT_POLICY)
        except (OSError, UnicodeError) as exc:
            raise Problem("LOCAL_BUDGET_CONFIG_ERROR", "Cannot read the local request budget", error_type=type(exc).__name__) from None
        try:
            supplied = json.loads(text)
        except ValueError:
            raise Problem("LOCAL_BUDGET_CONFIG_ERROR", "Local request budget must be valid JSON") from None
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_POLICY):
            raise Problem("LOCAL_BUDGET_CONFIG_ERROR", "Unknown local request budget keys")
        policy = {**DEFAULT_POLICY, **supplied}
        if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
            raise Problem("LOCAL_BUDGET_CONFIG_ERROR", "Local request budget schema_version must be 1")
        window = policy["window_seconds"]
        if type(window) is not int or not 1 <= window <= 366 * 86400:
            raise Problem("LOCAL_BUDGET_CONFIG_ERROR", "window_seconds must be an integer in 1..31622400")
        for key in LIMITS:
            value = policy[key]
            if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
                raise Problem("LOCAL_BUDGET_CONFIG_ERROR", f"{key} must be null or an integer in 0..1000000000000")
        return policy

    @staticmethod
    def _usage(con, cutoff):
        row = con.execute("""SELECT COUNT(*), COALESCE(SUM(estimated_cells),0),
            COALESCE(SUM(estimated_cells IS NULL),0) FROM query_budget_attempts
            WHERE reserved_at > ?""", (cutoff,)).fetchone()
        return {"reserved_native_calls": row[0], "known_estimated_cells": row[1],
                "unestimated_native_calls": row[2]}

    def inspect(self):
        try:
            policy = self.policy()
        except Problem as exc:
            return {"configured": True, "valid": False, "native_requests_allowed": False,
                    "code": exc.code, "message": str(exc), "policy_file": str(self.path),
                    "account_quota": False}
        with self.store.connect() as con:
            usage = self._usage(con, self.clock() - policy["window_seconds"])
            started = con.execute("SELECT tracking_started_at FROM query_budget_meta WHERE id=1").fetchone()[0]
        return {"configured": any(policy[key] is not None for key in LIMITS), "valid": True,
                "policy_file": str(self.path), "policy": policy, "usage": usage,
                "tracking_started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
                "shared_scope": "same_data_directory_and_budget_aware_server_version",
                "account_quota": False, "older_receipts_imported_as_usage": False,
                "note": "Local reservations include failures, timeouts and unfinished attempts; cache/local reads cost no reservation. Other profiles, older servers and non-MCP Wind usage are excluded. Estimated cells are not Wind billing units. Null limits disable enforcement; zero explicitly blocks that budget. Unknown output shapes are rejected when a cell limit is configured."}

    def reserve(self, method, arguments):
        policy = self.policy()  # Reload for each attempt, without a server restart.
        estimate = estimate_cells(method, arguments)
        cells = estimate["estimated_cells"]
        stamp = self.clock()
        with self.store.connect() as con:
            # Cross-process atomic check-and-reserve. Outstanding attempts count.
            con.execute("BEGIN IMMEDIATE")
            usage = self._usage(con, stamp - policy["window_seconds"])
            cell_limits = (policy["max_estimated_cells"], policy["max_estimated_cells_per_request"])
            if cells is None and any(value is not None for value in cell_limits):
                raise Problem("LOCAL_BUDGET_UNESTIMATED", "Cannot estimate this request's output grid under the configured cell budget; no Wind request was sent",
                              method=method, estimate=estimate, account_quota=False)
            if policy["max_estimated_cells"] is not None and usage["unestimated_native_calls"]:
                raise Problem("LOCAL_BUDGET_UNESTIMATED", "Earlier attempts in this local window have unknown output size; the cumulative cell budget cannot be evaluated",
                              usage=usage, account_quota=False)
            checks = [
                ("max_native_calls", usage["reserved_native_calls"] + 1),
                ("max_estimated_cells", usage["known_estimated_cells"] + (cells or 0)),
                ("max_estimated_cells_per_request", cells or 0),
            ]
            for key, proposed in checks:
                if policy[key] is not None and proposed > policy[key]:
                    raise Problem("LOCAL_BUDGET_EXCEEDED", "Configured local request budget would be exceeded; no Wind request was sent",
                                  budget=key, limit=policy[key], proposed=proposed, usage=usage,
                                  estimate=estimate, account_quota=False, automatic_retry_recommended=False)
            reservation_id = uuid.uuid4().hex
            con.execute("INSERT INTO query_budget_attempts VALUES(?,?,?,?,?,NULL)",
                        (reservation_id, stamp, method, cells, "reserved"))
        return {"reservation_id": reservation_id, "estimate": estimate,
                "native_request_reserved": True, "account_quota": False}

    def finish(self, reservation, receipt_id=None, state="returned"):
        with self.store.connect() as con:
            con.execute("UPDATE query_budget_attempts SET state=?,receipt_id=? WHERE id=?",
                        (state, receipt_id, reservation["reservation_id"]))
