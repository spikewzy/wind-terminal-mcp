import copy
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.security_names import inputs, resolve_inputs
from wind_bridge.service import Service
from wind_bridge.storage import Store


def entity(name, code, kind, **extra):
    return {"entity": name, "fullName": name, "id": code, "type": kind,
            "startIndex": 0, "endIndex": len(name) - 1, **extra}


class SecurityNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.entities = {"贵州茅台": [entity("贵州茅台", "600519.SH", "stockCN")],
                         "苹果": [entity("苹果", "AAPL.O", "stockUS")],
                         "沪深300": [entity("沪深300", "000300.SH", "indicator")],
                         "沪深300ETF": [entity("沪深300ETF", "510300.SH", "fund")]}
        self.paragraph_override = None
        self.application_status = 200
        self.market_error = 0
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        if method == "wai":
            paragraphs = self.paragraph_override if self.paragraph_override is not None else [self.entities.get(n, []) for n in arguments["input"].split("\n")]
            payload = {"status": "0", "body": {"status_code": self.application_status, "succeed": True, "data": paragraphs}}
            raw = {"ErrorCode": 0, "Codes": ["fer"], "Fields": ["details"], "Times": ["2026-09-30"],
                   "Data": [[json.dumps(payload)]]}
        else:
            codes = arguments["codes"].split(",")
            fields = arguments["fields"].upper().split(",")
            times = ["2026-09-28", "2026-09-29"] if method == "wsd" else ["2026-09-29T09:31:00", "2026-09-29T09:32:00"]
            count = len(codes) if method == "wsq" else len(times)
            raw = {"ErrorCode": self.market_error, "Codes": codes, "Fields": fields, "Times": times,
                   "Data": [[10.0] * count for _ in fields]}
        return {"ok": True, "raw": raw}

    def resolve(self, value, domain="stock_data", multiple=False):
        return resolve_inputs(self.service, value, domain, multiple=multiple)

    def assert_problem(self, fn, code):
        with self.assertRaises(Problem) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_explicit_codes_do_not_trigger_recognition(self):
        codes, evidence = self.resolve("600519.sh,000001.SZ", multiple=True)
        self.assertEqual(codes, "600519.SH,000001.SZ")
        self.assertIsNone(evidence)
        self.assertEqual(self.calls, [])

    def test_names_and_codes_keep_order_with_one_batched_recognition(self):
        codes, evidence = self.resolve("苹果,000001.SZ,贵州茅台", multiple=True)
        self.assertEqual(codes, "AAPL.O,000001.SZ,600519.SH")
        self.assertEqual(self.calls[0][1]["input"], "苹果\n贵州茅台")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(evidence["mappings"][1]["status"], "explicit_code")
        self.assertFalse(evidence["suffix_guessed"])
        self.assertEqual(evidence["data_source_id"], "wind_terminal_api")
        native = self.store.read(evidence["receipt_id"])
        self.assertEqual(native["sha256"], evidence["raw_sha256"])
        derived = self.store.page(evidence["derived_receipt_id"])
        self.assertEqual(derived["response"]["derived"]["resolved_codes"], codes.split(","))

    def test_duplicate_top_candidate_is_one_code_but_other_market_is_not(self):
        root = self.entities["贵州茅台"][0]
        root["candidateEntities"] = [copy.deepcopy(root)]
        self.assertEqual(self.resolve("贵州茅台")[0], "600519.SH")
        root["candidateEntities"].append({"id": "600519.HK", "type": "stockHK", "entity": "贵州茅台", "matchConfidence": 0})
        root["matchConfidence"] = 1
        problem = self.assert_problem(lambda: self.resolve("贵州茅台"), "SECURITY_SELECTION_REQUIRED")
        result = problem.details["security_resolution"]
        self.assertEqual(len(result["mappings"][0]["candidates"]), 2)
        self.assertFalse(result["confidence_ranking_used"])
        self.assertFalse(problem.details["market_query_executed"])
        self.assertEqual(self.store.read(result["derived_receipt_id"])["response"]["derived"]["status"], "selection_required")

    def test_substring_missing_span_or_wrong_entity_text_is_not_selected(self):
        for replacement in [entity("茅台", "600519.SH", "stockCN"),
                            entity("贵州茅台", "600519.SH", "stockCN", endIndex=2),
                            {"entity": "贵州茅台", "type": "stockCN", "id": "600519.SH"}]:
            self.entities["贵州茅台"] = [replacement]
            problem = self.assert_problem(lambda: self.resolve("贵州茅台"), "SECURITY_SELECTION_REQUIRED")
            self.assertIsNone(problem.details["security_resolution"]["mappings"][0]["selected_code"])

    def test_financial_ids_and_wrong_security_domains_are_not_coerced(self):
        self.entities["营业收入"] = [entity("营业收入", "255842", "stockBondIndex", param="600519.SH")]
        for name, domain in [("营业收入", "stock_data"), ("沪深300", "stock_data"), ("贵州茅台", "index_data")]:
            self.assert_problem(lambda: self.resolve(name, domain), "SECURITY_SELECTION_REQUIRED")
        self.assertTrue(all(method == "wai" for method, _ in self.calls))
        self.assertEqual(self.resolve("沪深300", "index_data")[0], "000300.SH")
        self.assertEqual(self.resolve("沪深300ETF", "fund_data")[0], "510300.SH")

    def test_off_exchange_fund_not_used_for_price_query(self):
        self.entities["场外基金"] = [entity("场外基金", "005827.OF", "fund")]
        problem = self.assert_problem(lambda: self.resolve("场外基金", "fund_data"), "SECURITY_SELECTION_REQUIRED")
        self.assertEqual(problem.details["security_resolution"]["mappings"][0]["rejected_candidates"][0]["reason"],
                         "off_exchange_fund_not_an_exchange_price_series")

    def test_paragraph_count_or_application_failure_stops_before_market(self):
        self.paragraph_override = []
        self.assert_problem(lambda: self.resolve("贵州茅台"), "SECURITY_RECOGNITION_ALIGNMENT")
        self.paragraph_override = None
        self.application_status = 500
        self.assert_problem(lambda: self.resolve("贵州茅台"), "WAI_APPLICATION_ERROR")
        self.assertTrue(all(method == "wai" for method, _ in self.calls))

    def test_unrecognized_member_stops_whole_batch(self):
        problem = self.assert_problem(lambda: execute(self.service, "stock_data", "get_stock_price_indicators",
            {"windcode": "贵州茅台,未知名称", "indexes": "最新成交价"}), "SECURITY_SELECTION_REQUIRED")
        self.assertEqual([row["status"] for row in problem.details["security_resolution"]["mappings"]], ["resolved", "unresolved"])
        self.assertEqual([method for method, _ in self.calls], ["wai"])

    def test_alias_and_explicit_code_collision_is_not_silently_deduplicated(self):
        problem = self.assert_problem(lambda: self.resolve("贵州茅台,600519.SH", multiple=True), "DUPLICATE_SECURITY_SELECTION")
        self.assertFalse(problem.details["market_query_executed"])
        self.assertEqual(len(self.calls), 1)

    def test_input_bounds_and_one_security_series_reject_before_query(self):
        for value, multiple in [(None, True), (" ", True), ("贵州茅台\n苹果", True), ("贵州茅台,", True),
                                ("600519.SH,600519.sh", True), (",".join(str(i) for i in range(51)), True),
                                ("贵州茅台,苹果", False), ("x" * 5001, True)]:
            self.assert_problem(lambda: self.resolve(value, multiple=multiple), "INVALID_PARAMS")
        self.assertEqual(len(inputs(",".join(f"A{i}.O" for i in range(50)), True)), 50)
        self.assertEqual(self.calls, [])

    def test_invalid_market_parameters_do_not_spend_recognition_query(self):
        for tool, params in [("get_stock_quote", {"windcode": "贵州茅台", "end": "2026-09-29"}),
                             ("get_stock_quote", {"windcode": "贵州茅台", "begin": "2026-02-30"}),
                             ("get_stock_price_indicators", {"windcode": "贵州茅台", "indexes": []})]:
            self.assert_problem(lambda: execute(self.service, "stock_data", tool, params), "INVALID_PARAMS")
        self.assert_problem(lambda: execute(self.service, "stock_data", "get_stock_price_indicators",
                            {"windcode": "贵州茅台", "indexes": "未映射字段"}), "UNVERIFIED_MAPPING")
        self.assertEqual(self.calls, [])

    def test_kline_selection_keeps_original_input_and_name_evidence(self):
        result = execute(self.service, "stock_data", "get_stock_kline", {"windcode": "贵州茅台",
            "begin_date": "2026-09-28", "end_date": "2026-09-29", "count": -1})
        self.assertEqual([method for method, _ in self.calls], ["wai", "wsd"])
        self.assertEqual(result["raw"]["Codes"], ["600519.SH"])
        self.assertEqual(result["raw"]["Times"], ["2026-09-29"])
        selected = self.store.read(result["derived_receipt_id"])["response"]["derived"]
        self.assertEqual(selected["security_resolution"]["input_text"], "贵州茅台")
        self.assertEqual(selected["alice_contract"]["params"]["windcode"], "贵州茅台")

    def test_quote_and_snapshot_routes_resolve_names(self):
        result = execute(self.service, "fund_data", "get_fund_quote", {"windcode": "沪深300ETF",
            "begin": "2026-09-29", "end": "2026-09-29", "count": -1})
        self.assertEqual(result["raw"]["Codes"], ["510300.SH"])
        result = execute(self.service, "index_data", "get_index_price_indicators",
            {"windcode": "沪深300,000001.SH", "indexes": "最新成交价"})
        self.assertEqual(result["raw"]["Codes"], ["000300.SH", "000001.SH"])
        self.assertEqual([method for method, _ in self.calls], ["wai", "wsi", "wai", "wsq"])

    def test_market_failure_retains_successful_resolution_without_fallback(self):
        self.market_error = -40521007
        problem = self.assert_problem(lambda: execute(self.service, "stock_data", "get_stock_price_indicators",
            {"windcode": "贵州茅台", "indexes": "最新成交价"}), "WIND_UPSTREAM_ERROR")
        self.assertEqual(problem.details["security_resolution"]["resolved_codes"], ["600519.SH"])
        self.assertEqual([method for method, _ in self.calls], ["wai", "wsq"])


if __name__ == "__main__":
    unittest.main()
