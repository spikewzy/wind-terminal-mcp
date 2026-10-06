import gzip
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.service import Service
from wind_bridge.storage import Store
from wind_bridge.universe import screen_universe


class UniverseScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls, self.fail_call, self.missing_code = [], None, None
        self.service = Service(self.store, Backend(self.store, self.fake))
        self.codes = [f"{i:06d}.SZ" for i in range(1, 131)]
        self.universe = self.save_universe(self.codes)
        self.plan = {"universe_receipt_id": self.universe, "fields": "close", "options": "tradeDate=20260929",
                     "conditions": [{"field": "close", "op": "gt", "value": 110}],
                     "sort_by": "close", "descending": True, "limit": 3, "evidence": "Test fixture"}

    def save_universe(self, codes, validated=True):
        saved = self.store.save("wset", {"tablename": "sectorconstituent", "options": "fixture"}, {"ok": True, "raw": {
            "ErrorCode": 0, "Fields": ["wind_code"], "Codes": [], "Times": ["2026-09-29"], "Data": [codes]}})
        if validated:
            self.store.validation(saved["receipt_id"], "passed", {})
        return saved["receipt_id"]

    def fake(self, method, args):
        self.calls.append((method, args))
        if len(self.calls) == self.fail_call:
            return {"ok": False, "code": "FIXTURE_PERMISSION_ERROR", "message": "Fixture failure"}
        codes = args["codes"].split(",")
        return {"ok": True, "raw": {"ErrorCode": 0, "Codes": codes, "Fields": ["CLOSE"], "Times": ["2026-09-29"],
                                    "Data": [[None if code == self.missing_code else int(code[:6]) for code in codes]]}}

    def test_resume_queries_all_members_and_sorts_globally_before_limit(self):
        first = screen_universe(self.service, self.plan)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["scanned_count"], 100)
        self.assertNotIn("rows", first)
        result = screen_universe(self.service, continuation_receipt_id=first["continuation_receipt_id"])
        self.assertEqual([len(args["codes"].split(",")) for _, args in self.calls], [50, 50, 30])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["matched_count"], 20)
        self.assertEqual([row["close"] for row in result["rows"]], [130, 129, 128])
        self.assertEqual(result["universe"], self.codes)
        self.assertEqual(len(result["input_receipt_ids"]), 4)
        self.assertFalse(result["point_in_time_safe"])
        self.assertFalse(result["full_market_coverage_certified"])

    def test_failed_batch_keeps_progress_and_does_not_retry_or_fallback(self):
        self.fail_call = 2
        failed = screen_universe(self.service, self.plan, batches_per_call=3)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["status"], "batch_failed")
        self.assertEqual(failed["scanned_count"], 50)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("receipt_id", failed["error"])
        self.fail_call = None
        result = screen_universe(self.service, continuation_receipt_id=failed["continuation_receipt_id"])
        self.assertTrue(result["screen_ready"])
        self.assertEqual(self.calls[2][1]["codes"].split(",")[0], "000051.SZ")

    def test_missing_data_is_explicit_and_not_filled(self):
        self.missing_code = "000130.SZ"
        result = screen_universe(self.service, self.plan, batches_per_call=3)
        self.assertEqual(result["excluded_unknown_codes"], [self.missing_code])
        self.assertEqual([row["close"] for row in result["rows"]], [129, 128, 127])

    def test_duplicate_or_unvalidated_universe_fails_before_native_query(self):
        for receipt_id, expected in [(self.save_universe(["000001.SZ", "000001.SZ"]), "DUPLICATE_UNIVERSE_CODES"),
                                      (self.save_universe(self.codes, False), "UNVALIDATED_INPUT_RECEIPT")]:
            with self.assertRaises(Problem) as error:
                screen_universe(self.service, {**self.plan, "universe_receipt_id": receipt_id})
            self.assertEqual(error.exception.code, expected)
        self.assertEqual(self.calls, [])

    def test_invalid_plan_cannot_consume_queries(self):
        for changes in [{"conditions": [{"field": "unknown", "op": "gt", "value": 0}]}, {"fields": "close,CLOSE"},
                        {"evidence": ""}, {"codes": "600519.SH"}, {"code_field": "missing"}]:
            with self.assertRaises(Problem):
                screen_universe(self.service, {**self.plan, **changes})
        self.assertEqual(self.calls, [])

    def rewrite_receipt(self, receipt_id, change):
        saved = self.store.read(receipt_id)
        saved.pop("sha256", None)
        change(saved)
        with gzip.open(self.store.receipts / (receipt_id + ".json.gz"), "wt") as stream:
            json.dump(saved, stream)

    def test_changed_universe_cannot_be_resumed(self):
        first = screen_universe(self.service, self.plan, batches_per_call=1)
        self.rewrite_receipt(self.universe, lambda r: r["response"]["raw"]["Data"][0].reverse())
        with self.assertRaises(Problem) as error:
            screen_universe(self.service, continuation_receipt_id=first["continuation_receipt_id"])
        self.assertEqual(error.exception.code, "UNIVERSE_CHANGED")
        self.assertEqual(len(self.calls), 1)

    def test_mixed_snapshot_options_cannot_enter_resumed_results(self):
        first = screen_universe(self.service, self.plan, batches_per_call=1)
        self.rewrite_receipt(first["snapshot_receipt_ids"][0], lambda r: r["arguments"].update(options="tradeDate=20200101"))
        with self.assertRaises(Problem) as error:
            screen_universe(self.service, continuation_receipt_id=first["continuation_receipt_id"])
        self.assertEqual(error.exception.code, "CHECKPOINT_QUERY_MISMATCH")
        self.assertEqual(len(self.calls), 1)

    def test_alice_search_contract_can_start_the_same_universe_workflow(self):
        result = execute(self.service, "stock_data", "search_stocks", {"question": "Fixture screen"},
                         {"method": "wss", "arguments": {"fields": "close", "options": "tradeDate=20260929"},
                          "universe_receipt_id": self.universe, "evidence": "Fixture",
                          "analysis": {"kind": "screen", "conditions": self.plan["conditions"], "sort_by": "close", "limit": 3}})
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["alice_contract"]["tool_name"], "search_stocks")
        self.assertFalse(result["semantic_equivalence_verified"])


if __name__ == "__main__":
    unittest.main()
