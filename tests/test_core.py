import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.requests import validate
from wind_bridge.service import Service
from wind_bridge.storage import Store


class WindCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.raw = {"ErrorCode": 0, "Codes": ["S0117164"], "Fields": ["CLOSE"],
                    "Times": ["2026-09-21", "2026-09-22"], "Data": [[10.0, None]]}
        self.backend = Backend(self.store, self.fake)
        # These provenance tests explicitly import the legacy sample catalog.
        self.service = Service(self.store, self.backend,
                               seed_path=Path(__file__).resolve().parents[1] / "references/agri-indicator-seed.json")

    def fake(self, method, args):
        self.calls.append((method, args))
        return {"ok": True, "raw": json.loads(json.dumps(self.raw))}

    def economic(self, **kwargs):
        return self.service.economic("S0117164", "2026-09-21", "2026-09-28", **kwargs)

    def test_native_source_and_metadata_source_stay_separate(self):
        result = self.economic()
        metric = result["metrics"][0]
        self.assertEqual(result["data_source_id"], "wind_terminal_api")
        self.assertEqual(metric["meta"]["metadata_provenance"]["metadata_source_id"], "alice_mcp")
        self.assertEqual(metric["source"], "汇易网")
        self.assertFalse(metric["point_in_time_safe"])
        self.assertEqual(metric["null_count"], 1)
        self.assertIsNone(metric["value"][1])

    def test_out_of_range_success_is_rejected_and_raw_preserved(self):
        self.raw["Times"] = ["2018-01-01", "2026-09-22"]
        with self.assertRaises(Problem) as raised:
            self.economic()
        self.assertEqual(raised.exception.code, "OUT_OF_RANGE_DATA")
        receipt = self.store.read(raised.exception.details["receipt_id"])
        self.assertEqual(receipt["response"]["raw"]["Times"][0], "2018-01-01")
        with self.store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM observations").fetchone()[0], 0)

    def test_shape_and_wrong_codes_cannot_be_misattributed(self):
        self.raw["Data"] = [[10.0]]
        with self.assertRaises(Problem) as raised:
            self.economic()
        self.assertEqual(raised.exception.code, "SHAPE_MISMATCH")
        self.raw["Codes"] = ["S0112897"]
        with self.assertRaises(Problem) as raised:
            self.economic()
        self.assertEqual(raised.exception.code, "CODE_MISMATCH")

    def test_ambiguous_natural_language_does_not_fetch(self):
        with self.assertRaises(Problem) as raised:
            self.service.economic("豆粕库存", "2026-09-21", "2026-09-28")
        self.assertEqual(raised.exception.code, "INDICATOR_SELECTION_REQUIRED")
        self.assertEqual(self.calls, [])

    def test_catalog_filters_do_not_turn_metadata_into_entitlement(self):
        result = self.service.catalog.search("大豆 港口 库存", filters={"source": "汇易网", "freq": "日"})
        self.assertEqual(result["metrics"][0]["code"], "S0117164")
        self.assertTrue(all(row["source"] == "汇易网" for row in result["metrics"]))
        self.assertTrue(all(row["windpy_access"] == "not_implied_by_catalog" for row in result["metrics"]))
        with self.assertRaises(Problem):
            self.service.catalog.search("？？")

    def test_failed_entitlement_stops_batch_without_fallback(self):
        self.raw.update(ErrorCode=-40521007, Data=[["权限验证不通过"]])
        with self.assertRaises(Problem) as raised:
            self.service.economic("S0117164,S0112897", "2026-09-21", "2026-09-28")
        self.assertEqual(raised.exception.details["wind_error_code"], -40521007)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "edb")

    def test_cache_is_opt_in_and_vintages_are_retained(self):
        first = self.economic()
        cached = self.economic(max_age_seconds=3600)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(cached["metrics"][0]["from_cache"])
        self.raw["Data"][0][0] = 12.0
        self.economic()
        self.assertEqual(len(self.calls), 2)
        revisions = self.store.revisions("S0117164", "2026-09-21", "2026-09-28")
        self.assertEqual([v["value"] for v in revisions[0]["versions"]], [10.0, 12.0])
        self.assertEqual(self.store.read(first["receipt_ids"][0])["response"]["raw"]["Data"][0][0], 10.0)

    def test_read_allowlist_rejects_code_and_trade_operations(self):
        for method in ["__dict__", "wupf", "eval", "torder"]:
            with self.assertRaises(Problem):
                validate(method, {})
        with self.assertRaises(Problem):
            validate("wsq", {"codes": "600519.SH", "fields": "rt_last", "func": "anything"})

    def test_receipt_path_traversal_is_rejected(self):
        with self.assertRaises(Problem):
            self.store.read("../../secret")

    def test_latest_n_does_not_pretend_missing_observations_exist(self):
        today = dt.date.today()
        self.raw["Times"] = [(today - dt.timedelta(days=2)).isoformat(), (today - dt.timedelta(days=1)).isoformat()]
        result = self.service.economic("S0117164", observation="10")
        self.assertEqual(result["warnings"][0]["type"], "INSUFFICIENT_OBSERVATIONS")
        self.assertEqual(result["warnings"][0]["returned"], 2)
        self.assertEqual(result["selection"]["mode"], "last_n_observation_dates_on_or_before_query_end")
        self.assertFalse(result["selection"]["publication_date_selection"])

    def test_table_dimensions_and_pagination_preserve_request_metadata(self):
        self.raw = {"ErrorCode": 0, "Codes": ["000300.SH"], "Fields": ["wind_code", "sec_name"],
                    "Times": ["2026-09-28"], "Data": [["A.SH", "B.SH", "C.SH"], ["A", "B", "C"]]}
        result = self.service.query("wset", {"tablename": "sectorconstituent", "options": "date=20260928"})
        self.assertIn("rectangular_table_dimensions", result["checks"])
        page = self.store.page(result["receipt_id"], offset=1, limit=1)
        self.assertEqual(page["page"]["axis"], "table_rows")
        self.assertEqual(page["response"]["raw"]["Data"], [["B.SH"], ["B"]])
        self.assertEqual(page["response"]["raw"]["Times"], ["2026-09-28"])
        self.assertEqual(page["response"]["raw"]["Codes"], ["000300.SH"])
        self.assertEqual(page["column_lengths"], [3, 3])
        self.assertEqual(self.store.read(result["receipt_id"])["response"]["raw"]["Data"][0], ["A.SH", "B.SH", "C.SH"])
        self.raw["Data"][1].pop()
        with self.assertRaises(Problem) as raised:
            self.service.query("wset", {"tablename": "sectorconstituent"})
        self.assertEqual(raised.exception.code, "SHAPE_MISMATCH")
        receipt = self.store.read(raised.exception.details["receipt_id"])
        self.assertEqual(len(receipt["response"]["raw"]["Data"][1]), 2)

    def test_snapshot_pagination_keeps_codes_aligned_with_values(self):
        self.raw = {"ErrorCode": 0, "Codes": ["600519.SH", "000001.SZ"], "Fields": ["CLOSE"],
                    "Times": ["2026-09-28"], "Data": [[100, 20]]}
        result = self.service.query("wss", {"codes": "600519.SH,000001.SZ", "fields": "close"})
        page = self.store.page(result["receipt_id"], offset=1, limit=1)
        self.assertEqual(page["response"]["raw"]["Codes"], ["000001.SZ"])
        self.assertEqual(page["response"]["raw"]["Data"], [[20]])
        with self.assertRaises(Problem):
            self.store.page(result["receipt_id"], offset=True)

    def test_table_date_observations_cover_all_rows_before_paging(self):
        self.raw = {"ErrorCode": 0, "Codes": ["1", "2"], "Fields": ["date", "wind_code"],
                    "Times": ["2026-09-30"], "Data": [["2026-09-01", "2026-09-29"], ["A.SH", "B.SH"]]}
        result = self.service.query("wset", {"tablename": "IndexConstituent", "options": "date=20260929"})
        observed = result["table_date_observations"]
        self.assertEqual(observed["date_columns"][0]["different_from_requested_date_count"], 1)
        self.assertFalse(result["validation_scope"]["requested_date_bounds"])
        page = self.store.page(result["receipt_id"], offset=1, limit=1)
        self.assertEqual(page["table_date_observations"], observed)
        self.assertEqual(page["response"]["raw"]["Data"][0], ["2026-09-29"])
        self.assertEqual(self.store.read(result["receipt_id"])["response"]["raw"], self.raw)

    def test_verified_wsi_amount_alias_preserves_raw_and_does_not_accept_other_fields(self):
        self.raw = {"ErrorCode": 0, "Codes": ["600519.SH"], "Fields": ["amount"],
                    "Times": ["2026-09-28T09:31:00"], "Data": [[12345.0]]}
        args = {"codes": "600519.SH", "fields": "amt", "beginTime": "2026-09-28 09:30:00", "endTime": "2026-09-28 15:00:00"}
        result = self.service.query("wsi", args)
        self.assertEqual(result["raw"]["Fields"], ["amount"])
        self.assertEqual(result["field_aliases"][0]["requested"], "AMT")
        self.assertEqual(result["field_aliases"][0]["returned"], "AMOUNT")
        self.assertEqual(self.store.read(result["receipt_id"])["response"]["raw"]["Fields"], ["amount"])
        self.raw["Fields"] = ["unrelated_field"]
        with self.assertRaises(Problem) as raised:
            self.service.query("wsi", args)
        self.assertEqual(raised.exception.code, "FIELD_MISMATCH")

    def test_dates_and_observation_are_mutually_exclusive(self):
        with self.assertRaises(Problem):
            self.economic(observation="10")
        self.assertEqual(self.calls, [])

    def test_alice_adjustment_enum_is_mapped_not_copied(self):
        self.raw = {"ErrorCode": 0, "Codes": ["600519.SH"], "Fields": ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "AMT"],
                    "Times": ["2026-09-21", "2026-09-22"], "Data": [[1, 2]] * 6}
        result = execute(self.service, "stock_data", "get_stock_kline", {
            "windcode": "600519.SH", "begin_date": "2026-09-21", "end_date": "2026-09-28", "aftype": "1", "count": -1})
        self.assertIn("PriceAdj=B", self.calls[0][1]["options"])
        self.assertEqual(result["raw"]["Times"], ["2026-09-22"])
        self.assertEqual(len(self.store.read(result["receipt_id"])["response"]["raw"]["Times"]), 2)

    def test_missing_field_mapping_is_not_silent_partial_success(self):
        with self.assertRaises(Problem) as raised:
            execute(self.service, "stock_data", "get_stock_price_indicators", {"windcode": "600519.SH", "indexes": "最新成交价,总市值1"})
        self.assertEqual(raised.exception.code, "UNVERIFIED_MAPPING")
        self.assertEqual(self.calls, [])

    def test_document_top_k_cannot_be_silently_ignored(self):
        with self.assertRaises(Problem) as raised:
            execute(self.service, "financial_docs", "get_financial_news", {"query": "Test query", "top_k": 3},
                    {"method": "wnd", "arguments": {"codes": "600519.SH", "beginTime": "2026-09-21", "endTime": "2026-09-28"}, "evidence": "Fixture"})
        self.assertEqual(raised.exception.code, "DOCUMENT_SEARCH_NOT_MIGRATED")
        self.assertEqual(self.calls, [])

    def test_alice_risk_plan_performs_analysis_instead_of_returning_only_prices(self):
        self.raw = {"ErrorCode": 0, "Codes": ["600519.SH"], "Fields": ["CLOSE"],
                    "Times": ["2026-09-21", "2026-09-22", "2026-09-23"], "Data": [[100, 110, 99]]}
        result = execute(self.service, "stock_data", "get_risk_metrics", {"question": "Test drawdown"},
                         {"method": "wsd", "arguments": {"codes": "600519.SH", "fields": "close", "beginTime": "2026-09-21", "endTime": "2026-09-28"},
                          "evidence": "Test fixture only", "analysis": {"kind": "series", "periods_per_year": 252, "window": 2}})
        self.assertAlmostEqual(result["statistics"]["maximum_drawdown"], -.1)
        self.assertEqual(result["processing"], "local_derived")
        self.assertFalse(result["semantic_equivalence_verified"])
        self.assertEqual(self.store.read(result["raw_input_receipt_id"])["method"], "wsd")


if __name__ == "__main__":
    unittest.main()
