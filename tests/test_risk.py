import copy
import datetime as dt
import gzip
import json
import math
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.kline import select_series_rows
from wind_bridge.risk import historical_tail, risk_analysis
from wind_bridge.service import Service
from wind_bridge.storage import Store


def prices_from_returns(returns):
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1 + value))
    return prices


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))

    def receipt(self, prices, dates=None, code="TEST.SH", valid=True, fields=None):
        dates = dates or [(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat() for i in range(len(prices))]
        raw = {"ErrorCode": 0, "Codes": [code], "Fields": fields or ["CLOSE"], "Times": dates, "Data": [prices]}
        args = {"codes": code, "fields": "close", "beginTime": dates[0], "endTime": dates[-1], "options": "Period=D;PriceAdj=F"}
        receipt = self.store.save("wsd", args, {"ok": True, "raw": raw})
        if valid:
            self.store.validation(receipt["receipt_id"], "passed", {"fixture": True})
        return receipt["receipt_id"]

    def test_hand_calculated_standalone_risk_and_saved_provenance(self):
        rid = self.receipt([100, 110, 99, 108.9])
        original = self.store.read(rid)
        result = risk_analysis(self.store, rid, 252)
        stats = result["statistics"]
        self.assertAlmostEqual(stats["total_return"], .089)
        self.assertAlmostEqual(stats["annualized_volatility"], math.sqrt(.04 / 3 * 252))
        self.assertAlmostEqual(stats["sharpe"], (.1 / 3) / math.sqrt(.04 / 3) * math.sqrt(252))
        self.assertAlmostEqual(stats["maximum_drawdown"], -.1)
        self.assertEqual(stats["negative_return_count"], 1)
        self.assertAlmostEqual(result["tail_risk"]["historical_var"], .1)
        self.assertAlmostEqual(result["tail_risk"]["historical_expected_shortfall"], .1)
        self.assertEqual(result["input_evidence"][0]["sha256"], original["sha256"])
        self.assertFalse(result["provider_metric_equivalence_verified"])
        self.assertEqual(result["data_source_id"], "wind_terminal_api")
        self.assertEqual(self.store.read(rid), original)
        saved = self.store.page(result["derived_receipt_id"], 1, 1)
        self.assertEqual(saved["response"]["derived"]["statistics"], stats)
        self.assertEqual(saved["response"]["derived"]["returns"], result["returns"])

    def test_ols_recovers_known_beta_and_alpha_with_nonzero_risk_free(self):
        rf = math.expm1(math.log1p(.04) / 12)
        benchmark_returns = [-.03, .02, .04, -.01, .01]
        asset_returns = [rf + .003 + 1.7 * (value - rf) for value in benchmark_returns]
        asset = self.receipt(prices_from_returns(asset_returns))
        benchmark = self.receipt(prices_from_returns(benchmark_returns), code="000300.SH")
        result = risk_analysis(self.store, asset, 12, benchmark_receipt_id=benchmark, annual_risk_free_rate=.04)
        stats = result["benchmark"]["statistics"]
        self.assertAlmostEqual(stats["beta"], 1.7)
        self.assertAlmostEqual(stats["jensen_alpha_per_period"], .003)
        self.assertAlmostEqual(stats["jensen_alpha_annualized_arithmetic"], .036)
        self.assertAlmostEqual(stats["correlation"], 1)
        self.assertAlmostEqual(stats["r_squared"], 1)
        self.assertEqual(result["input_receipt_ids"], [asset, benchmark])
        self.assertAlmostEqual(result["conventions"]["risk_free_per_period"], rf)

    def test_empirical_var_and_es_fractional_tail_and_ties(self):
        tail = historical_tail([-.5, -.1, 0, .1, .2], .7)
        self.assertEqual(tail["historical_var"], .1)
        self.assertAlmostEqual(tail["historical_expected_shortfall"], (.5 + .5 * .1) / 1.5)
        self.assertEqual(tail["tail_equivalent_observations"], 1.5)
        tied = historical_tail([-.5, -.1, -.1, .1, .2], .6)
        self.assertEqual(tied["historical_var"], .1)
        self.assertAlmostEqual(tied["historical_expected_shortfall"], .3)
        at_atom = historical_tail([-.5, -.1, 0, .1, .2], .8)
        self.assertEqual(at_atom["historical_var"], .1)
        self.assertEqual(at_atom["historical_expected_shortfall"], .5)

    def test_gains_are_not_floored_and_single_atom_tail_is_supported(self):
        result = historical_tail([.1, .2, .3], .999)
        self.assertEqual(result["historical_var"], -.1)
        self.assertEqual(result["historical_expected_shortfall"], -.1)
        self.assertFalse(result["loss_floor_applied"])

    def test_zero_variance_undefined_statistics_are_explicit(self):
        asset = self.receipt([100, 100, 100, 100])
        benchmark = self.receipt([100, 100, 100, 100], code="000300.SH")
        result = risk_analysis(self.store, asset, 252, benchmark_receipt_id=benchmark)
        self.assertIsNone(result["statistics"]["sharpe"])
        self.assertEqual(result["undefined_statistics"]["sharpe"], "zero_asset_return_volatility")
        for field in ["beta", "correlation", "r_squared", "jensen_alpha_per_period", "information_ratio"]:
            self.assertIsNone(result["benchmark"]["statistics"][field])
            self.assertIn(field, result["benchmark"]["undefined_statistics"])
        self.assertEqual(result["benchmark"]["statistics"]["tracking_error_annualized"], 0)
        self.assertIsNone(result["drawdown"]["peak_date"])
        self.assertEqual(result["statistics"]["zero_return_count"], 3)

    def test_same_asset_and_benchmark_have_zero_tracking_error(self):
        asset = self.receipt([100, 110, 99, 108.9])
        result = risk_analysis(self.store, asset, 252, benchmark_receipt_id=asset)
        self.assertAlmostEqual(result["benchmark"]["statistics"]["beta"], 1)
        self.assertAlmostEqual(result["benchmark"]["statistics"]["jensen_alpha_per_period"], 0)
        self.assertIsNone(result["benchmark"]["statistics"]["information_ratio"])

    def test_different_calendars_require_explicit_alignment(self):
        asset = self.receipt([100, 101, 140, 99, 102, 101])
        benchmark = self.receipt([100, 102, 98, 101, 104],
                                 dates=["2026-09-01", "2026-09-02", "2026-09-04", "2026-09-05", "2026-09-06"])
        with self.assertRaises(Problem) as error:
            risk_analysis(self.store, asset, 252, benchmark_receipt_id=benchmark)
        self.assertEqual(error.exception.code, "RISK_DATE_ALIGNMENT_REQUIRED")
        self.assertEqual(len(error.exception.details["asset_only_intervals"]), 2)
        result = risk_analysis(self.store, asset, 252, benchmark_receipt_id=benchmark, alignment="common_intervals")
        paired = result["benchmark"]
        self.assertEqual(paired["matched_return_count"], 3)
        self.assertAlmostEqual(paired["asset_returns"][1], 102 / 99 - 1)
        self.assertEqual(paired["matched_intervals"][1], ["2026-09-04T00:00:00", "2026-09-05T00:00:00"])
        self.assertEqual(len(paired["alignment"]["benchmark_only_intervals"]), 1)
        self.assertFalse(paired["alignment"]["prices_joined_before_returns"])
        self.assertEqual(result["statistics"]["return_count"], 5)
        self.assertAlmostEqual(result["statistics"]["maximum_drawdown"], 99 / 140 - 1)

    def test_no_or_insufficient_common_intervals_are_rejected(self):
        asset = self.receipt([100, 101, 99, 105])
        benchmark = self.receipt([100, 101, 99, 105], dates=[f"2026-08-{day:02d}" for day in range(1, 5)])
        with self.assertRaises(Problem) as error:
            risk_analysis(self.store, asset, 252, benchmark_receipt_id=benchmark, alignment="common_intervals")
        self.assertEqual(error.exception.code, "INSUFFICIENT_MATCHED_RETURNS")
        self.assertEqual(error.exception.details["matched_return_count"], 0)

    def test_iso_format_and_explicit_timezones_match_without_guessing(self):
        asset = self.receipt([100, 101, 99, 105], dates=[f"2026-09-{day:02d}T08:00:00+08:00" for day in range(1, 5)])
        benchmark = self.receipt([100, 101, 99, 105], dates=[f"2026-09-{day:02d}T00:00:00+00:00" for day in range(1, 5)])
        result = risk_analysis(self.store, asset, 252, benchmark_receipt_id=benchmark)
        self.assertEqual(result["benchmark"]["matched_return_count"], 3)
        naive = self.receipt([100, 101, 99, 105])
        with self.assertRaises(Problem):
            risk_analysis(self.store, asset, 252, benchmark_receipt_id=naive)
        naive_time = self.receipt([100, 101, 99, 105], dates=[f"2026-09-{day:02d}T00:00:00" for day in range(1, 5)])
        self.assertEqual(risk_analysis(self.store, naive, 252, benchmark_receipt_id=naive_time)["benchmark"]["matched_return_count"], 3)

    def test_drawdown_peak_trough_and_recovery(self):
        rid = self.receipt([100, 120, 90, 96, 120, 100])
        result = risk_analysis(self.store, rid, 252)["drawdown"]
        self.assertEqual(result["maximum_drawdown"], -.25)
        self.assertEqual([result[key] for key in ["peak_date", "trough_date", "recovery_date"]],
                         ["2026-09-02", "2026-09-03", "2026-09-05"])
        self.assertEqual(result["peak_to_trough_observations"], 1)
        self.assertEqual(result["peak_to_trough_calendar_days"], 1)
        self.assertEqual(result["trough_to_recovery_observations"], 2)
        self.assertFalse(result["unrecovered_at_sample_end"])
        unrecovered = risk_analysis(self.store, self.receipt([100, 120, 90, 96]), 252)["drawdown"]
        self.assertTrue(unrecovered["unrecovered_at_sample_end"])
        self.assertIsNone(unrecovered["recovery_date"])

    def test_missing_nonpositive_or_short_series_and_unvalidated_receipts_fail(self):
        for values in [[100, None, 101, 102], [100, 0, 101, 102], [100, -1, 101, 102],
                       [100, float("nan"), 101, 102], [100, 101, 102]]:
            with self.subTest(values=values), self.assertRaises(Problem):
                risk_analysis(self.store, self.receipt(values), 252)
        with self.assertRaises(Problem) as error:
            risk_analysis(self.store, self.receipt([100, 101, 102, 103], valid=False), 252)
        self.assertEqual(error.exception.code, "UNVALIDATED_INPUT_RECEIPT")

    def test_invalid_parameters_and_dates_fail(self):
        rid = self.receipt([100, 101, 99, 105])
        for params in [{"confidence": 0}, {"confidence": 1}, {"confidence": True}, {"periods_per_year": 0},
                       {"periods_per_year": float("inf")}, {"annual_risk_free_rate": -1},
                       {"alignment": "inner"}, {"alignment": "common_intervals"}, {"field": "absent"}]:
            with self.subTest(params=params), self.assertRaises(Problem):
                risk_analysis(self.store, rid, **{"periods_per_year": 252, **params})
        for dates in [["2026-09-01"] * 4, ["2026-09-01", "bad", "2026-09-03", "2026-09-04"],
                      ["2026-09-01", "2026-09-03", "2026-09-02", "2026-09-04"]]:
            with self.subTest(dates=dates), self.assertRaises(Problem):
                risk_analysis(self.store, self.receipt([100, 101, 99, 105], dates), 252)

    def test_selected_series_is_rebuilt_and_changed_selection_is_rejected(self):
        rid = self.receipt([100, 1000, 100, 110, 99, 108.9])
        native = self.store.read(rid)
        selected = select_series_rows(self.store, {"method": "wsd", "receipt_id": rid,
            "arguments": native["arguments"], "raw": native["response"]["raw"]}, -4)
        result = risk_analysis(self.store, selected["derived_receipt_id"], 252)
        self.assertAlmostEqual(result["statistics"]["maximum_drawdown"], -.1)
        self.assertEqual(result["statistics"]["observation_count"], 4)
        self.assertTrue(result["input_evidence"][0]["selection_rebuilt_from_native_receipt"])
        path = self.store.receipts / f"{selected['derived_receipt_id']}.json.gz"
        saved = self.store.read(selected["derived_receipt_id"])
        saved["response"]["derived"]["raw"]["Data"][0][0] = 500
        with gzip.open(path, "wt") as handle:
            json.dump(saved, handle)
        with self.assertRaises(Problem) as error:
            risk_analysis(self.store, selected["derived_receipt_id"], 252)
        self.assertEqual(error.exception.code, "ROW_SELECTION_MISMATCH")


class RiskCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.calls = []
        self.fail_benchmark = False
        self.service = Service(self.store, Backend(self.store, self.fake))
        self.plan = {"method": "wsd", "arguments": {"codes": "600519.SH", "fields": "close",
            "beginTime": "2026-09-01", "endTime": "2026-09-04", "options": "Period=D;PriceAdj=F"},
            "evidence": "test fixture", "analysis": {"kind": "risk", "periods_per_year": 252,
            "benchmark_request": {"method": "wsd", "arguments": {"codes": "000300.SH", "fields": "close",
            "beginTime": "2026-09-01", "endTime": "2026-09-04", "options": "Period=D;PriceAdj=U"}, "evidence": "test fixture"}}}

    def fake(self, method, args):
        self.calls.append((method, args))
        error = -40522017 if args["codes"] == "000300.SH" and self.fail_benchmark else 0
        return {"ok": True, "raw": {"ErrorCode": error, "Codes": [args["codes"]], "Fields": ["CLOSE"],
            "Times": [f"2026-09-{day:02d}" for day in range(1, 5)], "Data": [[100, 110, 99, 108.9]]}}

    def call(self, plan=None):
        return execute(self.service, "stock_data", "get_risk_metrics", {"question": "茅台与沪深300的区间风险指标"}, plan or self.plan)

    def test_compatibility_fetches_both_series_and_calculates_benchmark_metrics(self):
        result = self.call()
        self.assertEqual([args["codes"] for _, args in self.calls], ["600519.SH", "000300.SH"])
        self.assertAlmostEqual(result["benchmark"]["statistics"]["beta"], 1)
        self.assertEqual(result["alice_contract"]["tool_name"], "get_risk_metrics")
        self.assertEqual(len(result["input_receipt_ids"]), 2)
        self.assertEqual(result["benchmark_query"], self.plan["analysis"]["benchmark_request"])
        self.assertFalse(result["semantic_equivalence_verified"])

    def test_saved_benchmark_is_reused_without_a_second_native_query(self):
        benchmark = self.service.query("wsd", self.plan["analysis"]["benchmark_request"]["arguments"])
        self.calls.clear()
        plan = copy.deepcopy(self.plan)
        del plan["analysis"]["benchmark_request"]
        plan["analysis"]["benchmark_receipt_id"] = benchmark["receipt_id"]
        result = self.call(plan)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["input_receipt_ids"][1], benchmark["receipt_id"])

    def test_benchmark_failure_preserves_completed_stock_receipt(self):
        self.fail_benchmark = True
        with self.assertRaises(Problem) as error:
            self.call()
        self.assertEqual(error.exception.code, "WIND_UPSTREAM_ERROR")
        completed = error.exception.details["completed_input_receipt_ids"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(self.store.read(completed[0])["response"]["raw"]["Codes"], ["600519.SH"])
        self.assertEqual(self.store.read(error.exception.details["receipt_id"])["response"]["raw"]["ErrorCode"], -40522017)
        self.assertEqual(len(self.calls), 2)

    def test_invalid_risk_plan_is_rejected_before_any_native_query(self):
        for change in ["confidence", "two_benchmarks", "bad_method", "missing_field", "missing_receipt", "bad_main_method"]:
            plan = copy.deepcopy(self.plan)
            if change == "confidence":
                plan["analysis"]["confidence"] = 1
            elif change == "two_benchmarks":
                plan["analysis"]["benchmark_receipt_id"] = "0" * 32
            elif change == "bad_method":
                plan["analysis"]["benchmark_request"]["method"] = "wss"
            elif change == "missing_field":
                plan["analysis"]["benchmark_field"] = "open"
            elif change == "missing_receipt":
                del plan["analysis"]["benchmark_request"]
                plan["analysis"]["benchmark_receipt_id"] = "0" * 32
            else:
                plan["method"] = "wss"
            with self.subTest(change=change), self.assertRaises(Problem):
                self.call(plan)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
