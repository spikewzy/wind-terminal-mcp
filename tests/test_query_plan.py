import itertools
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.common import Problem
from wind_bridge.query_plan import plan_queries
from wind_bridge.requests import validate
from wind_bridge.storage import Store


class QueryPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name))

    def args(self, codes=90, fields=3):
        return {"codes": [f"TEST{i}.SHF" for i in range(codes)],
                "fields": [f"field{i}" for i in range(fields)],
                "beginTime": "2026-01-01", "endTime": "2026-09-29",
                "options": "order=40;Days=Alldays;Fill=Blank"}

    def assert_exact_coverage(self, args, method="wsd"):
        plan = plan_queries(self.store, method, args, limit=100)
        requests = list(plan["requests"])
        next_offset = plan["next_offset"]
        while next_offset is not None:
            page = plan_queries(self.store, method, args, limit=100, offset=next_offset)
            self.assertEqual(page["plan_id"], plan["plan_id"])
            requests.extend(page["requests"])
            next_offset = page["next_offset"]
        pairs = []
        for request in requests:
            self.assertEqual(validate(method, request["arguments"]), request["arguments"])
            self.assertEqual(request["arguments"]["options"], args["options"])
            if method == "wsd":
                self.assertEqual(request["arguments"]["beginTime"], args["beginTime"])
                self.assertEqual(request["arguments"]["endTime"], args["endTime"])
            c0, c1 = request["code_slice"]
            f0, f1 = request["field_slice"]
            self.assertEqual(request["arguments"]["codes"].split(","), args["codes"][c0:c1])
            self.assertEqual(request["arguments"]["fields"].split(","), args["fields"][f0:f1])
            pairs.extend(itertools.product(range(c0, c1), range(f0, f1)))
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(set(pairs), set(itertools.product(range(len(args["codes"])), range(len(args["fields"])))))
        self.assertEqual(len(requests), plan["total_requests"])
        self.assertEqual([r["index"] for r in requests], list(range(len(requests))))
        return plan

    def test_90_product_three_field_history_uses_six_calls(self):
        plan = self.assert_exact_coverage(self.args())
        self.assertEqual(plan["total_requests"], 6)
        self.assertEqual(plan["selected_strategy"]["strategy"], "per_field")
        self.assertFalse(plan["executed"])
        self.assertEqual(plan["native_calls"], 0)
        self.assertEqual(plan["input_shape"]["code_field_pairs"], 270)
        self.assertFalse(plan["execution_policy"]["automatic_retry"])
        self.assertFalse(plan["globally_minimal_proven"])
        self.assertEqual(plan["data_volume_estimate"]["estimated_cells"], 90 * 3 * 272)
        self.assertEqual(sum(r["data_volume_estimate"]["estimated_cells"] for r in plan["requests"]),
                         plan["data_volume_estimate"]["estimated_cells"])

    def test_small_code_dimension_uses_code_loop(self):
        plan = self.assert_exact_coverage(self.args(3, 8))
        self.assertEqual(plan["total_requests"], 3)
        self.assertEqual(plan["selected_strategy"]["strategy"], "per_code")

    def test_ragged_code_tail_avoids_redundant_single_field_calls(self):
        plan = self.assert_exact_coverage(self.args(51, 2))
        self.assertEqual(plan["total_requests"], 3)
        self.assertEqual(plan["selected_strategy"]["strategy"], "field_groups_then_code_tail")

    def test_ragged_field_tail_preserves_all_101_fields(self):
        plan = self.assert_exact_coverage(self.args(2, 101))
        self.assertEqual(plan["total_requests"], 3)
        self.assertEqual(plan["selected_strategy"]["strategy"], "code_groups_then_field_tail")

    def test_both_axis_boundaries_and_paging_cover_every_pair_once(self):
        for codes, fields in [(1, 1), (49, 99), (50, 100), (51, 101), (101, 201), (153, 4)]:
            with self.subTest(codes=codes, fields=fields):
                self.assert_exact_coverage(self.args(codes, fields))

    def test_snapshot_uses_rectangles_without_changing_trade_date(self):
        args = self.args(51, 101)
        args.pop("beginTime")
        args.pop("endTime")
        args["options"] = "tradeDate=20260929;rptType=2;unit=1"
        plan = self.assert_exact_coverage(args, "wss")
        self.assertEqual(plan["total_requests"], 4)
        self.assertEqual(plan["input_shape"]["time_observations"], 1)

    def test_pages_have_stable_identity_but_changed_options_do_not(self):
        args = self.args()
        first = plan_queries(self.store, "wsd", args, limit=1)
        second = plan_queries(self.store, "wsd", args, limit=3, offset=1)
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(second["requests"][0]["index"], 1)
        changed = plan_queries(self.store, "wsd", {**args, "options": "order=1"})
        self.assertNotEqual(changed["plan_id"], first["plan_id"])
        empty = plan_queries(self.store, "wsd", args, offset=6)
        self.assertEqual(empty["requests"], [])
        self.assertIsNone(empty["next_offset"])

    def test_quota_failure_is_visible_without_retries_or_new_receipts(self):
        receipt = self.store.save("wsd", {}, {"ok": True, "raw": {
            "ErrorCode": -40522017, "Data": [["CWSDService: quota exceeded."]]}})
        before = list(self.store.receipts.iterdir())
        plan = plan_queries(self.store, "wsd", self.args())
        self.assertEqual(plan["quota_observation"]["last_observed_error"]["receipt_id"], receipt["receipt_id"])
        self.assertIsNone(plan["quota_observation"]["current_reset_at"])
        self.assertFalse(plan["quota_observation"]["live_quota_checked"])
        self.assertEqual(list(self.store.receipts.iterdir()), before)
        self.assertEqual(plan["native_calls"], 0)

    def test_no_implicit_calendar_fill_report_type_or_cross_method(self):
        args = {**self.args(2, 1), "fields": ["oper_rev"], "options": "Period=Q"}
        plan = plan_queries(self.store, "wsd", args)
        self.assertTrue(all(r["method"] == "wsd" and r["arguments"]["options"] == "Period=Q" for r in plan["requests"]))
        self.assertFalse(plan["invariants"]["time_range_split"])
        self.assertFalse(plan["invariants"]["missing_values_filled"])
        explicit = {**args, "options": "Period=Q;Days=Alldays;rptType=1;showblank=0"}
        self.assertEqual(plan_queries(self.store, "wsd", explicit)["requests"][0]["arguments"]["options"], explicit["options"])

    def test_bad_keys_dates_identifiers_and_unsupported_methods_fail_locally(self):
        for method, args in [("edb", self.args()), ("wupf", self.args()), ("wsd", None),
                             ("wsd", {**self.args(), "beginTime": "bad"}),
                             ("wsd", {**self.args(), "usedf": True}),
                             ("wsd", {**self.args(), "codes": "CU00.SHF，IF00.CFE"}),
                             ("wsd", {**self.args(), "fields": ["close", "CLOSE"]})]:
            with self.subTest(method=method, args=args), self.assertRaises(Problem):
                plan_queries(self.store, method, args)
        self.assertEqual(list(self.store.receipts.iterdir()), [])

    def test_limits_do_not_truncate_input(self):
        for args in [self.args(10001, 1), self.args(1, 501), self.args(10000, 500)]:
            with self.subTest(codes=len(args["codes"]), fields=len(args["fields"])), self.assertRaises(Problem):
                plan_queries(self.store, "wsd", args)
        for limit, offset in [(0, 0), (101, 0), (True, 0), (20, -1)]:
            with self.assertRaises(Problem):
                plan_queries(self.store, "wsd", self.args(), limit=limit, offset=offset)


if __name__ == "__main__":
    unittest.main()
