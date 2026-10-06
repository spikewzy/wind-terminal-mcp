import math
from pathlib import Path
import tempfile
import unittest

from wind_bridge.analytics import aggregate_snapshot, combine_edb, compile_expression, screen_snapshot, series_analysis
from wind_bridge.common import Problem
from wind_bridge.storage import Store


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))

    def receipt(self, method, codes, fields, data, dates=None, validated=True):
        raw = {"ErrorCode": 0, "Codes": codes, "Fields": fields, "Data": data, "Times": dates or ["2026-09-28"]}
        receipt = self.store.save(method, {"options": "PriceAdj=U"}, {"ok": True, "raw": raw})
        if validated:
            self.store.validation(receipt["receipt_id"], "passed", {"fixture": True})
        return receipt["receipt_id"]

    def prices(self, values, validated=True):
        return self.receipt("wsd", ["TEST.SH"], ["CLOSE"], [values],
                            [f"2026-09-{day:02d}" for day in range(21, 21 + len(values))], validated)

    def test_returns_drawdown_and_annualization_match_hand_calculation(self):
        receipt = self.prices([100, 110, 99, 108.9])
        result = series_analysis(self.store, receipt, "close", 2, 252, 0)
        self.assertAlmostEqual(result["statistics"]["total_return"], .089)
        self.assertAlmostEqual(result["statistics"]["maximum_drawdown"], -.1)
        self.assertAlmostEqual(result["statistics"]["annualized_volatility"], math.sqrt(.04 / 3) * math.sqrt(252))
        self.assertAlmostEqual(result["statistics"]["sharpe"], (.1 / 3) / math.sqrt(.04 / 3) * math.sqrt(252))
        self.assertEqual(result["moving_average"][:3], [None, 105, 104.5])
        self.assertAlmostEqual(result["momentum"][2], -.01)
        self.assertEqual(result["input_receipt_ids"], [receipt])
        saved = self.store.read(result["derived_receipt_id"])
        self.assertEqual(saved["response"]["derived"]["processing"], "local_derived")
        self.assertEqual(self.store.read(receipt)["response"]["raw"]["Data"], [[100, 110, 99, 108.9]])

    def test_unvalidated_raw_and_missing_prices_cannot_enter_analysis(self):
        for receipt, code in [(self.prices([100, 101, 102], False), "UNVALIDATED_INPUT_RECEIPT"),
                              (self.prices([100, None, 102]), "NON_NUMERIC_INPUT")]:
            with self.assertRaises(Problem) as error:
                series_analysis(self.store, receipt, "close", 2, 252, 0)
            self.assertEqual(error.exception.code, code)

    def test_invalid_window_and_annualization_rejected(self):
        receipt = self.prices([100, 101, 102])
        for window, annual in [(True, 252), (2.5, 252), (4, 252), (2, 0), (2, float("nan"))]:
            with self.assertRaises(Problem):
                series_analysis(self.store, receipt, "close", window, annual, 0)

    def test_screen_keeps_unknowns_explicit_and_limits_after_sorting(self):
        receipt = self.receipt("wss", ["A.SH", "B.SH", "C.SH", "D.SH"], ["PE", "SIZE"], [[10, None, 20, None], [1, 2, 3, -1]])
        result = screen_snapshot(self.store, receipt, [{"field": "pe", "op": "lt", "value": 30},
                                                      {"field": "size", "op": "gt", "value": 0}], "size", True, 1)
        self.assertEqual([r["code"] for r in result["rows"]], ["C.SH"])
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["excluded_unknown_codes"], ["B.SH"])
        self.assertEqual(result["universe_count"], 4)

    def test_weighted_mean_and_missing_data_policy(self):
        receipt = self.receipt("wss", ["A.SH", "B.SH"], ["PRICE", "WEIGHT"], [[10, 20], [1, 3]])
        result = aggregate_snapshot(self.store, receipt, "price", "weighted_mean", "weight")
        self.assertEqual(result["value"], 17.5)
        self.assertIsNone(result["unit"])
        receipt = self.receipt("wss", ["A.SH", "B.SH"], ["PRICE"], [[10, None]])
        with self.assertRaises(Problem) as error:
            aggregate_snapshot(self.store, receipt, "price", "mean")
        self.assertEqual(error.exception.code, "NON_NUMERIC_INPUT")

    def test_expression_tree_rejects_hidden_calls_unknown_names_and_attributes(self):
        for expression in ["1/(x-x)+__import__('os')", "1/(x-x)+unknown", "x.real", "x[0]", "x**2", "True+x", "[x for x in y]"]:
            with self.assertRaises(Problem):
                compile_expression(expression, {"x"})
        self.assertEqual(compile_expression("-(x - 3) * 2 / 4", {"x"})({"x": 5}), -1)

    def test_edb_combine_uses_exact_dates_and_preserves_input_provenance(self):
        self.store.put_indicator({"code": "S0117164", "name": "Test stock", "unit": "吨", "source": "汇易网", "freq": "日"},
                                 {"metadata_source_id": "alice_mcp", "evidence": "fixture only"})
        first = self.receipt("edb", ["S0117164"], ["CLOSE"], [[12, 15, 0]], ["2026-09-21", "2026-09-22", "2026-09-23"])
        second = self.receipt("edb", ["S0112897"], ["CLOSE"], [[10, 0, 20]], ["2026-09-22", "2026-09-23", "2026-09-24"])
        result = combine_edb(self.store, {"spot": first, "future": second}, "spot/future-1")
        self.assertEqual(result["date"], ["2026-09-22", "2026-09-23"])
        self.assertEqual(result["value"], [.5, None])
        self.assertEqual(result["issues"], [{"date": "2026-09-23", "code": "DIVISION_BY_ZERO"}])
        self.assertEqual(result["input_metadata"]["spot"]["metadata"]["source"], "汇易网")
        self.assertEqual(result["input_metadata"]["spot"]["metadata_scope"], "current_catalog_at_analysis")
        self.assertEqual(result["input_metadata"]["spot"]["metadata"]["metadata_provenance"]["metadata_source_id"], "alice_mcp")
        self.assertEqual(result["data_source_id"], "wind_terminal_api")
        self.assertFalse(result["point_in_time_safe"])
        self.assertIsNone(result["unit"])

    def test_nonoverlapping_series_do_not_forward_fill(self):
        first = self.receipt("edb", ["S0117164"], ["CLOSE"], [[10]], ["2026-08-31"])
        second = self.receipt("edb", ["S0112897"], ["CLOSE"], [[20]], ["2026-09-01"])
        with self.assertRaises(Problem) as error:
            combine_edb(self.store, {"a": first, "b": second}, "a-b")
        self.assertEqual(error.exception.code, "NO_COMMON_OBSERVATIONS")


if __name__ == "__main__":
    unittest.main()
