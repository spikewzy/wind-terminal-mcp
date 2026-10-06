import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.analytics import series_analysis
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.service import Service
from wind_bridge.storage import Store


class KlineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.dates = ["2024-08-30", "2024-09-02", "2024-09-03", "2024-09-04"]
        self.statuses = ["交易", "交易", "停牌一天", "停牌一天"]
        self.values = {"open": [5, 5.3, 4.98, 4.98], "high": [5.2, 5.3, 4.98, 4.98],
                       "low": [4.9, 4.95, 4.98, 4.98], "close": [5.1, 4.98, 4.98, 4.98],
                       "volume": [100, 200, 0, 0], "amt": [510, 1020, 0, 0]}
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        fields = arguments["fields"].split(",")
        data = [self.statuses if field == "trade_status" else self.values[field] for field in fields]
        return {"ok": True, "raw": {"ErrorCode": 0, "Codes": ["601989.SH"],
                                    "Fields": [f.upper() for f in fields], "Times": list(self.dates),
                                    "Data": copy.deepcopy(data)}}

    def query(self, **overrides):
        return execute(self.service, "stock_data", "get_stock_kline",
                       {"windcode": "601989.SH", "begin_date": "2024-08-30", "end_date": "2024-09-06",
                        "aftype": "2", "issusp": "0", **overrides})

    def test_suspensions_are_filtered_before_negative_count(self):
        result = self.query(count=-1)
        self.assertEqual(result["raw"]["Times"], ["2024-09-02"])
        self.assertEqual(result["raw"]["Data"][3], [4.98])
        self.assertEqual(result["row_selection"]["steps"], ["exclude_full_day_suspensions", "apply_count"])
        self.assertEqual(result["row_selection"]["excluded_suspension_count"], 2)
        self.assertEqual(result["row_selection"]["eligible_row_count"], 2)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("trade_status", self.calls[0][1]["fields"])

    def test_positive_count_and_original_and_derived_receipts_are_distinct(self):
        result = self.query(count=1)
        original = self.store.read(result["raw_input_receipt_id"])
        derived = self.store.page(result["derived_receipt_id"])
        self.assertEqual(original["response"]["raw"]["Times"], self.dates)
        self.assertEqual(original["response"]["raw"]["Data"][-1], self.statuses)
        self.assertEqual(derived["method"], "series_row_selection")
        self.assertEqual(derived["response"]["derived"]["raw"], result["raw"])
        self.assertEqual(derived["response"]["derived"]["alice_contract"], result["alice_contract"])
        self.assertEqual(result["raw"]["Times"], ["2024-08-30"])
        self.assertNotEqual(result["receipt_id"], result["derived_receipt_id"])
        self.assertTrue(result["raw_is_selected_view"])
        self.assertTrue(result["raw_receipt_unchanged"])

    def test_included_suspensions_do_not_fetch_status_or_change_raw(self):
        result = self.query(issusp="1")
        self.assertNotIn("trade_status", self.calls[0][1]["fields"])
        self.assertEqual(result["raw"]["Times"], self.dates)
        self.assertNotIn("derived_receipt_id", result)

    def test_zero_volume_and_missing_price_are_not_suspension_proxies(self):
        self.values["volume"][0] = 0
        self.values["close"][0] = None
        result = self.query()
        self.assertEqual(result["raw"]["Times"], self.dates[:2])
        self.assertIsNone(result["raw"]["Data"][3][0])
        self.assertEqual(result["raw"]["Data"][4][0], 0)
        self.assertFalse(result["row_selection"]["price_or_volume_used_as_status"])

    def test_fully_suspended_interval_returns_empty_selected_view(self):
        self.statuses = ["停牌一天"] * len(self.dates)
        result = self.query(count=-3)
        self.assertEqual(result["raw"]["Times"], [])
        self.assertTrue(all(not column for column in result["raw"]["Data"]))
        self.assertEqual(result["row_selection"]["excluded_suspension_count"], 4)
        self.assertEqual(len(self.store.read(result["receipt_id"])["response"]["raw"]["Times"]), 4)

    def test_unknown_partial_or_missing_status_rejects_even_beyond_count(self):
        for status in [None, "", "停牌半天", "未上市", "unknown", 0]:
            with self.subTest(status=status):
                self.statuses[-1] = status
                with self.assertRaises(Problem) as raised:
                    self.query(count=1)
                self.assertEqual(raised.exception.code, "UNVERIFIED_TRADING_STATUS")
                receipt = self.store.read(raised.exception.details["receipt_id"])
                self.assertEqual(receipt["response"]["raw"]["Data"][-1][-1], status)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM receipts WHERE method='series_row_selection'").fetchone()[0], 0)

    def test_unsupported_periods_are_rejected_before_native_queries(self):
        for period in ["1w", "1mo", "1q", "6mo", "1y", "1min", "60min", "120min", "240min"]:
            with self.subTest(period=period):
                with self.assertRaises(Problem) as raised:
                    self.query(period=period)
                self.assertEqual(raised.exception.code, "UNVERIFIED_MAPPING")
        self.assertEqual(self.calls, [])

    def test_invalid_suspension_values_and_dates_reject_before_queries(self):
        for params in [{"issusp": None}, {"issusp": 0}, {"issusp": False}, {"issusp": "2"},
                       {"begin_date": "2024-08-30 00:00:00"}, {"end_date": "2024-08-29"}]:
            with self.subTest(params=params):
                with self.assertRaises(Problem) as raised:
                    self.query(**params)
                self.assertEqual(raised.exception.code, "INVALID_PARAMS")
        self.assertEqual(self.calls, [])

    def test_count_only_selection_is_saved_without_suspension_claims(self):
        result = self.query(issusp="1", count=-1)
        self.assertEqual(result["raw"]["Times"], [self.dates[-1]])
        self.assertEqual(result["row_selection"]["steps"], ["apply_count"])
        self.assertNotIn("status_evidence", result["row_selection"])
        self.assertEqual(self.store.read(result["derived_receipt_id"])["response"]["derived"]["raw"], result["raw"])

    def test_analysis_uses_selected_prices_and_keeps_source_options(self):
        self.statuses = ["交易", "停牌一天", "交易", "交易"]
        self.values["close"] = [100, 1000, 110, 99]
        selected = self.query()
        result = series_analysis(self.store, selected["derived_receipt_id"], "close", 2, 252, 0)
        self.assertEqual(result["date"], [self.dates[0], *self.dates[2:]])
        self.assertAlmostEqual(result["statistics"]["maximum_drawdown"], -0.1)
        self.assertEqual(result["input_receipt_ids"], [selected["derived_receipt_id"]])
        self.assertEqual(result["raw_input_receipt_id"], selected["receipt_id"])
        self.assertEqual(result["conventions"]["input_options"], "Period=D;PriceAdj=U")
        self.assertTrue(result["conventions"]["selection_rebuilt_from_native_receipt"])

    def test_analysis_rejects_changed_selected_view(self):
        selected = self.query(issusp="1", count=-3)
        path = self.store.receipts / f"{selected['derived_receipt_id']}.json.gz"
        receipt = self.store.read(selected["derived_receipt_id"])
        receipt["response"]["derived"]["raw"]["Data"][3][0] = 999
        with gzip.open(path, "wt") as stream:
            json.dump(receipt, stream)
        with self.assertRaises(Problem) as raised:
            series_analysis(self.store, selected["derived_receipt_id"], "close", 2, 252, 0)
        self.assertEqual(raised.exception.code, "ROW_SELECTION_MISMATCH")

    def test_analysis_rejects_changed_native_source(self):
        selected = self.query(issusp="1", count=-3)
        path = self.store.receipts / f"{selected['receipt_id']}.json.gz"
        receipt = self.store.read(selected["receipt_id"])
        receipt["response"]["raw"]["Data"][3][0] = 888
        with gzip.open(path, "wt") as stream:
            json.dump(receipt, stream)
        with self.assertRaises(Problem) as raised:
            series_analysis(self.store, selected["derived_receipt_id"], "close", 2, 252, 0)
        self.assertEqual(raised.exception.code, "ROW_SELECTION_SOURCE_CHANGED")


if __name__ == "__main__":
    unittest.main()
