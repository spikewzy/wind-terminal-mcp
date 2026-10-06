import concurrent.futures
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.query_budget import QueryBudget, estimate_cells
from wind_bridge.storage import Store


ARGS = {"codes": "AL00.SHF", "fields": "oi_nvoi", "beginTime": "2026-09-29",
        "endTime": "2026-09-29", "options": "order=40"}


def simultaneous_reservation(directory):
    budget = QueryBudget(Store(Path(directory)))
    try:
        budget.reserve("wsd", ARGS)
        return "reserved"
    except Problem as exc:
        return exc.code


class QueryBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name))
        self.calls = []
        self.backend = Backend(self.store, self.runner)

    def runner(self, method, arguments):
        self.calls.append((method, arguments))
        return {"ok": True, "raw": {"ErrorCode": 0, "Codes": ["AL00.SHF"],
                "Fields": ["OI_NVOI"], "Times": ["2026-09-29"], "Data": [[7466]]}}

    def configure(self, **values):
        self.backend.budget.path.write_text(json.dumps(values), encoding="utf-8")

    def test_unconfigured_records_attempts_without_claiming_account_quota(self):
        result = self.backend.request("wsd", ARGS)
        status = self.backend.status(False)["local_request_budget"]
        self.assertFalse(status["configured"])
        self.assertFalse(status["account_quota"])
        self.assertFalse(status["older_receipts_imported_as_usage"])
        self.assertEqual(status["usage"], {"reserved_native_calls": 1, "known_estimated_cells": 1, "unestimated_native_calls": 0})
        with self.store.connect() as con:
            row = con.execute("SELECT receipt_id FROM query_budget_attempts WHERE id=?",
                              (result["local_request_budget"]["reservation_id"],)).fetchone()
        self.assertEqual(row[0], result["receipt_id"])

    def test_per_request_rejects_large_history_before_runner_or_receipt(self):
        self.configure(max_estimated_cells_per_request=100)
        args = {**ARGS, "beginTime": "2000-01-01"}
        with self.assertRaises(Problem) as raised:
            self.backend.request("wsd", args)
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_EXCEEDED")
        self.assertFalse(raised.exception.details["account_quota"])
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.store.receipts.iterdir()), [])
        self.assertEqual(self.backend.budget.inspect()["usage"]["reserved_native_calls"], 0)

    def test_call_and_cumulative_cell_caps_are_shared_across_instances(self):
        self.configure(max_native_calls=4, max_estimated_cells=1)
        self.backend.request("wsd", ARGS)
        other = Backend(Store(self.store.directory), self.runner)
        with self.assertRaises(Problem) as raised:
            other.request("wsd", ARGS)
        self.assertEqual(raised.exception.details["budget"], "max_estimated_cells")
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_processes_cannot_spend_one_call_twice(self):
        self.configure(max_native_calls=1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
            results = list(pool.map(simultaneous_reservation, [str(self.store.directory)] * 8))
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("LOCAL_BUDGET_EXCEEDED"), 7)

    def test_timeout_and_unfinished_attempts_are_not_refunded(self):
        self.configure(max_native_calls=1)
        def timed_out(method, args):
            self.calls.append(method)
            raise Problem("WIND_TIMEOUT", "timed out")
        failing = Backend(self.store, timed_out)
        with self.assertRaises(Problem) as raised:
            failing.request("wsd", ARGS)
        self.assertEqual(raised.exception.code, "WIND_TIMEOUT")
        with self.assertRaises(Problem) as raised:
            self.backend.request("wsd", ARGS)
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_EXCEEDED")
        self.assertEqual(len(self.calls), 1)

    def test_cache_and_local_status_are_available_with_zero_budget(self):
        first = self.backend.request("wsd", ARGS)
        self.configure(max_native_calls=0)
        cached = self.backend.request("wsd", ARGS, max_age_seconds=60)
        self.assertTrue(cached["from_cache"])
        self.assertEqual(cached["receipt_id"], first["receipt_id"])
        self.assertFalse(cached["local_request_budget"]["native_request_reserved"])
        self.backend.status(False)
        with self.assertRaises(Problem) as raised:
            self.backend.status(True)
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_EXCEEDED")
        self.assertEqual(len(self.calls), 1)

    def test_malformed_config_blocks_native_but_preserves_local_diagnostics(self):
        for payload in ['bad', '[]', '{"max_native_calls":true}', '{"max_native_calls":-1}',
                        '{"unexpected":1}', '{"schema_version":2}', '{"window_seconds":0}']:
            self.backend.budget.path.write_text(payload, encoding="utf-8")
            with self.subTest(payload=payload), self.assertRaises(Problem) as raised:
                self.backend.request("wsd", ARGS)
            self.assertEqual(raised.exception.code, "LOCAL_BUDGET_CONFIG_ERROR")
            self.assertFalse(self.backend.status(False)["local_request_budget"]["valid"])
        self.assertEqual(self.calls, [])

    def test_rolling_window_and_policy_reload_preserve_reservations(self):
        self.configure(window_seconds=10, max_native_calls=1)
        current = [100.0]
        budget = QueryBudget(self.store, clock=lambda: current[0])
        reservation = budget.reserve("wsd", ARGS)
        current[0] = 109.0
        with self.assertRaises(Problem):
            budget.reserve("wsd", ARGS)
        current[0] = 110.0
        budget.reserve("wsd", ARGS)
        self.configure(window_seconds=100, max_native_calls=1)
        with self.assertRaises(Problem):
            budget.reserve("wsd", ARGS)
        with self.store.connect() as con:
            self.assertIsNotNone(con.execute("SELECT id FROM query_budget_attempts WHERE id=?", (reservation["reservation_id"],)).fetchone())

    def test_unknown_shapes_require_call_only_budget(self):
        self.configure(max_estimated_cells=10)
        with self.assertRaises(Problem) as raised:
            self.backend.request("wset", {"tablename": "sectorconstituent"})
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_UNESTIMATED")
        self.assertEqual(self.calls, [])
        self.configure(max_native_calls=1)
        self.backend.request("wset", {"tablename": "sectorconstituent"})
        self.assertEqual(self.backend.budget.inspect()["usage"]["unestimated_native_calls"], 1)
        self.configure(max_estimated_cells=10)
        with self.assertRaises(Problem) as raised:
            self.backend.request("wsd", ARGS)
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_UNESTIMATED")
        self.assertEqual(len(self.calls), 1)

    def test_grid_estimate_keeps_weekends_and_does_not_claim_billing(self):
        estimate = estimate_cells("wsd", {**ARGS, "beginTime": "2026-09-25", "endTime": "2026-09-29"})
        self.assertEqual(estimate["estimated_cells"], 5)
        self.assertFalse(estimate["billing_estimate"])
        self.assertEqual(estimate_cells("wsd", {**ARGS, "fields": "open,close"})["estimated_cells"], 2)
        self.assertEqual(estimate_cells("wss", {"codes": "CU00.SHF,AL00.SHF", "fields": "close,oi"})["estimated_cells"], 4)
        for options in ["Period=T", "Period=D;period=M", "Period:D", "BarSize=1"]:
            self.assertIsNone(estimate_cells("wsd", {**ARGS, "options": options})["estimated_cells"])
        for method in ["wsi", "wst", "wset", "wai"]:
            self.assertIsNone(estimate_cells(method, ARGS)["estimated_cells"])

    def test_service_edb_batch_stops_at_shared_budget_with_completed_receipts(self):
        from wind_bridge.service import Service
        self.configure(max_native_calls=1)
        def edb_runner(method, args):
            self.calls.append(args)
            return {"ok": True, "raw": {"ErrorCode": 0, "Codes": [args["codes"]],
                    "Fields": ["CLOSE"], "Times": ["2026-09-29"], "Data": [[1]]}}
        service = Service(self.store, Backend(self.store, edb_runner))
        with self.assertRaises(Problem) as raised:
            service.economic("M0000001,M0000002,M0000003", "2026-09-29", "2026-09-29")
        self.assertEqual(raised.exception.code, "LOCAL_BUDGET_EXCEEDED")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(raised.exception.details["completed_metrics"][0]["value"], [1])
        self.assertEqual(raised.exception.details["stopped_before_codes"], ["M0000002", "M0000003"])
