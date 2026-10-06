"""Explicit risk conventions over validated Wind price receipts."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, ROUND_CEILING
import math
import statistics

from .analytics import field_name, number, raw_receipt, save_derived
from .common import Problem


def risk_parameters(periods_per_year, annual_risk_free_rate, confidence, alignment):
    annualization = number(periods_per_year, "periods_per_year")
    rf = number(annual_risk_free_rate, "annual_risk_free_rate")
    probability = number(confidence, "confidence")
    if not 0 < annualization <= 100000 or rf <= -1 or not 0 < probability < 1:
        raise Problem("INVALID_PARAMS", "Require 0<periods_per_year<=100000, annual_risk_free_rate>-1 and 0<confidence<1")
    if not isinstance(alignment, str) or alignment not in {"strict", "common_intervals"}:
        raise Problem("INVALID_PARAMS", "alignment must be strict or common_intervals")
    try:
        rf_period = math.expm1(math.log1p(rf) / annualization)
    except OverflowError:
        raise Problem("INVALID_PARAMS", "Risk-free conversion overflowed; check the rate and frequency") from None
    return annualization, number(rf_period, "per-period risk-free rate"), probability


def price_series(store, receipt_id, field):
    receipt, raw = raw_receipt(store, receipt_id, {"wsd", "wses", "wsi"})
    field = field_name(field)
    if len(raw.get("Codes", [])) != 1:
        raise Problem("ONE_SERIES_REQUIRED", "Risk analysis requires one security in each input receipt")
    fields = [str(value).casefold() for value in raw.get("Fields", [])]
    if fields.count(field) != 1:
        raise Problem("MISSING_FIELD", "Risk price field must occur exactly once", field=field, fields=fields)
    values = raw["Data"][fields.index(field)]
    dates = raw.get("Times", [])
    if len(values) != len(dates):
        raise Problem("SHAPE_MISMATCH", "Price and date axes differ")
    prices = [number(value, field) for value in values]
    if len(prices) < 4 or any(value <= 0 for value in prices):
        raise Problem("INVALID_PRICE_SERIES", "Need at least four positive finite price observations; missing values are not filled or dropped")
    try:
        timestamps = [dt.datetime.fromisoformat(value) for value in dates]
    except (TypeError, ValueError):
        raise Problem("INVALID_RETURNED_DATE", "Risk input has an invalid timestamp") from None
    awareness = {value.tzinfo is not None for value in timestamps}
    if len(awareness) != 1:
        raise Problem("INVALID_RETURNED_DATE", "Risk input mixes timestamps with and without timezone offsets")
    aware = awareness.pop()
    if aware:
        timestamps = [value.astimezone(dt.timezone.utc) for value in timestamps]
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise Problem("INVALID_RETURNED_DATE", "Risk input timestamps must be unique and increasing")
    keys = [value.isoformat() for value in timestamps]
    returns = [number(b / a - 1, "price return") for a, b in zip(prices, prices[1:])]
    evidence = {"receipt_id": receipt_id, "sha256": receipt["sha256"], "fetched_at": receipt["fetched_at"],
                "method": receipt["method"], "code": raw["Codes"][0], "field": field,
                "arguments": receipt["arguments"], "price_count": len(prices),
                "timezone_basis": "explicit_offsets_normalized_to_UTC" if aware else "naive_as_returned_no_timezone_inferred"}
    if receipt["method"] == "series_row_selection":
        selected = receipt["response"]["derived"]
        evidence.update(raw_input_receipt_id=selected["raw_input_receipt_id"],
                        raw_input_sha256=selected["raw_input_sha256"],
                        native_arguments=selected["arguments"], row_selection=selected["row_selection"],
                        selection_rebuilt_from_native_receipt=True)
    return {"evidence": evidence, "dates": dates, "timestamps": timestamps, "keys": keys,
            "prices": prices, "returns": returns, "intervals": list(zip(keys, keys[1:]))}


def historical_tail(returns, confidence):
    """Integrate the equal-weight empirical loss quantile, including a partial atom."""
    losses = sorted(-value for value in returns)
    n = len(losses)
    probability = Decimal(str(confidence))
    rank = int((probability * n).to_integral_value(rounding=ROUND_CEILING))
    tail_mass = Decimal(n) * (1 - probability)
    whole = int(tail_mass)
    fraction = float(tail_mass - whole)
    worst_first = losses[::-1]
    if whole == 0:
        expected_shortfall = worst_first[0]
    else:
        numerator = math.fsum(worst_first[:whole])
        if fraction:
            numerator += fraction * worst_first[whole]
        expected_shortfall = numerator / float(tail_mass)
    return {"confidence": confidence, "historical_var": losses[rank - 1],
            "historical_expected_shortfall": expected_shortfall,
            "return_count": n, "tail_equivalent_observations": float(tail_mass),
            "horizon": "one_input_observation_interval", "loss_unit": "fraction",
            "quantile_method": "equal_weight_empirical_inverse_cdf_nearest_rank",
            "expected_shortfall_method": "mean_of_worst_1_minus_confidence_probability_mass_with_fractional_boundary_weight",
            "loss_floor_applied": False, "horizon_scaling_applied": False}


def drawdown(prices, dates, timestamps):
    peak = peak_index = 0
    minimum = 0.0
    selected_peak = selected_trough = None
    values = []
    for index, price in enumerate(prices):
        if price >= prices[peak]:
            peak = peak_index = index
        loss = price / prices[peak] - 1
        values.append(loss)
        if loss < minimum:
            minimum, selected_peak, selected_trough = loss, peak_index, index
    result = {"maximum_drawdown": minimum, "series": values, "peak_date": None,
              "trough_date": None, "recovery_date": None, "peak_to_trough_observations": None,
              "peak_to_trough_calendar_days": None, "trough_to_recovery_observations": None,
              "unrecovered_at_sample_end": False}
    if selected_peak is not None:
        recovery = next((index for index in range(selected_trough + 1, len(prices))
                         if prices[index] >= prices[selected_peak]), None)
        result.update(peak_date=dates[selected_peak], trough_date=dates[selected_trough],
                      recovery_date=dates[recovery] if recovery is not None else None,
                      peak_to_trough_observations=selected_trough - selected_peak,
                      peak_to_trough_calendar_days=(timestamps[selected_trough] - timestamps[selected_peak]).total_seconds() / 86400,
                      trough_to_recovery_observations=recovery - selected_trough if recovery is not None else None,
                      unrecovered_at_sample_end=recovery is None)
    return result


def benchmark_statistics(asset, benchmark, annualization, rf_period, alignment):
    asset_map = dict(zip(asset["intervals"], asset["returns"]))
    benchmark_map = dict(zip(benchmark["intervals"], benchmark["returns"]))
    asset_only = [list(pair) for pair in asset_map if pair not in benchmark_map]
    benchmark_only = [list(pair) for pair in benchmark_map if pair not in asset_map]
    if alignment == "strict" and (asset_only or benchmark_only):
        raise Problem("RISK_DATE_ALIGNMENT_REQUIRED", "Price-return intervals differ; use common_intervals explicitly after reviewing the calendars",
                      asset_only_intervals=asset_only, benchmark_only_intervals=benchmark_only)
    pairs = [pair for pair in asset_map if pair in benchmark_map]
    if len(pairs) < 3:
        raise Problem("INSUFFICIENT_MATCHED_RETURNS", "Need at least three returns with identical start AND end timestamps", matched_return_count=len(pairs))
    ar = [asset_map[pair] for pair in pairs]
    br = [benchmark_map[pair] for pair in pairs]
    av, bv = statistics.variance(ar), statistics.variance(br)
    covariance = statistics.covariance(ar, br)
    am, bm = statistics.mean(ar), statistics.mean(br)
    beta = covariance / bv if bv else None
    correlation = max(-1.0, min(1.0, covariance / math.sqrt(av) / math.sqrt(bv))) if av and bv else None
    alpha = am - rf_period - beta * (bm - rf_period) if beta is not None else None
    active = [a - b for a, b in zip(ar, br)]
    tracking = statistics.stdev(active)
    undefined = {}
    if not bv:
        undefined.update(beta="zero_benchmark_variance", jensen_alpha_per_period="zero_benchmark_variance",
                         jensen_alpha_annualized_arithmetic="zero_benchmark_variance")
    if correlation is None:
        undefined.update(correlation="zero_asset_or_benchmark_variance", r_squared="zero_asset_or_benchmark_variance")
    if not tracking:
        undefined["information_ratio"] = "zero_active_return_volatility"
    return {"code": benchmark["evidence"]["code"], "matched_return_count": len(pairs),
            "matched_intervals": [list(pair) for pair in pairs], "asset_returns": ar, "benchmark_returns": br,
            "alignment": {"mode": alignment, "compared": "consecutive_return_start_and_end_timestamps",
                          "asset_only_intervals": asset_only, "benchmark_only_intervals": benchmark_only,
                          "prices_joined_before_returns": False, "fill_applied": False,
                          "standalone_asset_statistics_use_all_asset_observations": True},
            "statistics": {"beta": beta, "correlation": correlation,
                           "r_squared": correlation ** 2 if correlation is not None else None,
                           "jensen_alpha_per_period": alpha,
                           "jensen_alpha_annualized_arithmetic": alpha * annualization if alpha is not None else None,
                           "tracking_error_annualized": tracking * math.sqrt(annualization),
                           "information_ratio": statistics.mean(active) / tracking * math.sqrt(annualization) if tracking else None},
            "undefined_statistics": undefined}


def risk_analysis(store, receipt_id, periods_per_year, field="close", benchmark_receipt_id=None,
                  benchmark_field="close", annual_risk_free_rate=0.0, confidence=0.95, alignment="strict"):
    annualization, rf_period, probability = risk_parameters(periods_per_year, annual_risk_free_rate, confidence, alignment)
    if benchmark_receipt_id is None and alignment != "strict":
        raise Problem("INVALID_PARAMS", "Alignment selection requires a benchmark receipt")
    asset = price_series(store, receipt_id, field)
    returns, prices = asset["returns"], asset["prices"]
    mean, volatility = statistics.mean(returns), statistics.stdev(returns)
    dd = drawdown(prices, asset["dates"], asset["timestamps"])
    undefined = {"sharpe": "zero_asset_return_volatility"} if not volatility else {}
    result = {"input_receipt_ids": [receipt_id], "input_evidence": [asset["evidence"]],
              "code": asset["evidence"]["code"], "date": asset["dates"], "returns": returns,
              "return_intervals": [list(pair) for pair in asset["intervals"]],
              "statistics": {"observation_count": len(prices), "return_count": len(returns),
                             "total_return": prices[-1] / prices[0] - 1,
                             "mean_return_per_period": mean, "volatility_per_period": volatility,
                             "annualized_volatility": volatility * math.sqrt(annualization),
                             "sharpe": (mean - rf_period) / volatility * math.sqrt(annualization) if volatility else None,
                             "maximum_drawdown": dd["maximum_drawdown"],
                             "negative_return_count": sum(value < 0 for value in returns),
                             "zero_return_count": sum(value == 0 for value in returns),
                             "positive_return_count": sum(value > 0 for value in returns),
                             "worst_return": min(returns), "best_return": max(returns)},
              "tail_risk": historical_tail(returns, probability), "drawdown": dd,
              "undefined_statistics": undefined, "benchmark": None,
              "conventions": {"formula_version": 1, "return_unit": "fraction", "returns": "simple_consecutive_observed_price_returns",
                              "volatility": "sample_standard_deviation_ddof_1", "periods_per_year": annualization,
                              "annual_risk_free_rate": annual_risk_free_rate, "risk_free_per_period": rf_period,
                              "beta": "OLS_slope_with_intercept_covariance_over_benchmark_variance",
                              "alpha": "mean_asset_excess_minus_beta_times_mean_benchmark_excess; arithmetic_annualization",
                              "var": "empirical_inverse_CDF_of_negative_returns; may_be_negative_if_tail_is_a_gain",
                              "es": "integral_of_empirical_loss_quantiles_above_confidence_divided_by_tail_probability",
                              "missing_policy": "reject_missing_nonfinite_and_nonpositive_prices",
                              "date_matching": "normalize_ISO_format_and_explicit_offsets_only; no_timezone_inference",
                              "adjustment_and_currency": "caller_must_verify_each_input_basis; no_conversion_or_equivalence_inferred",
                              "count_unit": "observed_return_intervals_not_calendar_days",
                              "sampling": "caller_supplied_annualization; irregular_intervals_are_not_certified_as_equal_duration",
                              "scope": "describes_supplied_price_series; not_strategy_performance_or_provider_metric_equivalence"},
              "point_in_time_safe": False, "provider_metric_equivalence_verified": False}
    if benchmark_receipt_id is not None:
        benchmark = price_series(store, benchmark_receipt_id, benchmark_field)
        result["benchmark"] = benchmark_statistics(asset, benchmark, annualization, rf_period, alignment)
        result["input_receipt_ids"].append(benchmark_receipt_id)
        result["input_evidence"].append(benchmark["evidence"])
    # Do not let JSON sanitation silently turn an overflow into a reported null.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise Problem("NUMERIC_OVERFLOW", "Risk calculation produced a nonfinite result")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(result)
    params = {"receipt_id": receipt_id, "periods_per_year": periods_per_year, "field": field,
              "benchmark_receipt_id": benchmark_receipt_id, "benchmark_field": benchmark_field,
              "annual_risk_free_rate": annual_risk_free_rate, "confidence": confidence, "alignment": alignment}
    return save_derived(store, "risk_analysis", params, result)
