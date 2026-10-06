"""Alice contract migration without impersonating Alice or guessing unknown fields."""
from __future__ import annotations

import json
import math
import re

from .analytics import aggregate_snapshot, screen_snapshot, series_analysis
from .common import ROOT, Problem, date_range, envelope, iso_date
from .fundamental_questions import FIELDS as FINANCIAL_QUESTION_FIELDS, execute_fundamentals
from .kline import select_series_rows
from .native_support import inspect_native_support
from .requests import csv, validate
from .risk import price_series, risk_analysis, risk_parameters
from .security_names import DOMAIN_TYPES, resolve_inputs
from .universe import screen_universe

MANIFEST = json.loads((ROOT / "references/alice-tool-manifest.json").read_text(encoding="utf-8"))
PERIODS = {"1d": "D", "1w": "W", "1mo": "M", "1q": "Q", "6mo": "S", "1y": "Y"}
PRICE_FIELDS = {
    "最新成交价": "rt_last", "前收盘价": "rt_pre_close", "今日开盘价": "rt_open",
    "今日最高价": "rt_high", "今日最低价": "rt_low", "涨跌幅": "rt_pct_chg",
    "涨跌": "rt_chg", "成交量": "rt_vol", "成交额": "rt_amt",
    "最新交易日": "rt_date", "交易时间": "rt_time", "买一价": "rt_bid1", "卖一价": "rt_ask1",
}
ROUTES = {
    "get_risk_metrics": ["wsd", "wss"], "get_stock_events": ["wss", "wset"],
    "get_stock_basicinfo": ["wss"], "get_stock_equity_holders": ["wss", "wset"],
    "get_stock_fundamentals": ["wss", "wsd"], "get_stock_technicals": ["wsd", "wss"],
    "search_stocks": ["wss", "weqs", "wset", "wai"],
    "get_fund_financials": ["wss", "wsd"], "get_fund_holdings": ["wss", "wset"],
    "get_fund_company_info": ["wss", "wset"], "get_fund_info": ["wss"],
    "get_fund_holders": ["wss", "wset"], "get_fund_performance": ["wsd", "wss"],
    "search_funds": ["wss", "wset", "wai"], "get_index_technicals": ["wsd", "wss"],
    "get_index_fundamentals": ["wss", "wsd", "wsee", "wses"],
    "get_index_basicinfo": ["wss", "wset"], "get_bond_basicinfo": ["wss"],
    "get_bond_issuer_info": ["wss"], "get_bond_market_data": ["wsd", "wss"],
    "get_bond_financial_data": ["wss", "wsd"],
    "get_company_announcements": ["wset", "wnd", "wnc"],
    "get_financial_news": ["wnd", "wnc", "wnq"],
    "get_financial_data": ["wsd", "wss", "edb", "wset"],
}


def capabilities():
    rows = []
    native_runtime = inspect_native_support()
    for domain, names in MANIFEST.items():
        for name in names:
            if name == "search_economic_indicator":
                status, methods, gap = "local_catalog_search", [], "Local catalog only; full Wind metadata discovery remains incomplete"
            elif name == "query_economic_indicator_data":
                status, methods, gap = "implemented_with_validation", ["edb"], "Arbitrary natural language requires explicit indicator selection; units and vintages not certified"
            elif name.endswith("_kline"):
                status, methods, gap = "parameter_subset", ["wai", "wsd", "wsi"], "Daily issusp=0 filters verified trade_status labels before count; other periods and status labels, 120/240-minute periods, afdate, VWAP and turnover outputs need additional verified mapping"
                if name == "get_index_kline":
                    gap += "; CSI300 trade_status was null in the live sample, so its daily issusp=0 request fails explicitly"
            elif name.endswith("_price_indicators"):
                status, methods, gap = "field_subset", ["wai", "wsq"], "Only PRICE_FIELDS mapping is implemented; other Alice indicators are not guessed"
            elif name.endswith("_quote"):
                status, methods, gap = "parameter_subset", ["wai", "wsq", "wsi"], "Missing dates use the security's reported RT_DATE; complete VWAP/turnover output semantics still require verification"
            elif name in {"search_stocks", "search_funds"}:
                status, methods, gap = "resumable_universe_screen_plan", ROUTES[name], "WSET/WEQS universe receipts can drive complete batched WSS screening with global sorting; the caller must verify universe, fields, options and historical availability"
            elif name == "get_risk_metrics":
                status, methods, gap = "explicit_query_and_benchmark_risk_plan", ROUTES[name], "Price-based risk, empirical VaR/ES and benchmark OLS statistics are available with explicit conventions and receipt evidence; not natural-language or provider-metric equivalence"
            elif name == "get_stock_fundamentals":
                status, methods, gap = "automatic_statement_questions_and_verified_plans", ["wai", *ROUTES[name]], "Nine statement fields support bounded questions with explicit report dates and yuan unit; statement scope is explicit or required by attributable-profit meaning. Typed mainland securities and all remaining text are checked. Other fields, markets, derived growth, valuations and as-of queries still require verified plans"
            elif name in {"get_stock_technicals", "get_index_technicals", "get_financial_data"}:
                status, methods, gap = "explicit_query_and_analysis_plan", ROUTES[name], "Validated receipt analysis is available for SMA/momentum/risk, explicit-universe screens and aggregates; full natural-language and full-market semantics are not replicated"
            elif domain == "financial_docs":
                status, methods, gap = "not_migrated", ROUTES[name], "Native news SDK candidates exist, but Alice document search and relevance-ranked top_k are not reproduced; the compatibility call fails explicitly"
                if set(methods) & set(native_runtime["unavailable_methods"]):
                    gap = "News methods return NULL unconditionally in the fingerprint-matched installed Mac SDK; WSET alternatives and Alice document relevance search remain unverified"
            else:
                status, methods, gap = "requires_verified_query_plan", ROUTES[name], "Native entry exists, but field/table/query semantics need verification; not full Alice parity"
            rows.append({"server_type": domain, "tool_name": name, "status": status,
                         "windpy_candidates": methods, "remaining_gap": gap})
    return envelope(alice_tool_count=len(rows), full_parity=False, mappings=rows, native_runtime=native_runtime,
                    security_name_resolution={"contracts": [row["tool_name"] for row in rows if row["tool_name"].endswith(("_kline", "_quote", "_price_indicators"))],
                                              "method": "wai", "function": "fer",
                                              "allowed_types": {domain: sorted(types) for domain, types in DOMAIN_TYPES.items()},
                                              "whole_input_span_required": True, "all_candidates_checked": True,
                                              "ambiguity_policy": "return_candidates_without_market_query",
                                              "explicit_codes_bypass_recognition": True,
                                              "separate_resolution_receipt": True,
                                              "evidence": "verification/security-name-probe.json"},
                    kline_row_selection={"exclusion_periods": ["1d"], "verified_daily_statuses": {"交易": "keep", "停牌一天": "exclude"},
                                         "unknown_status_policy": "reject_with_raw_receipt", "filter_before_count": True,
                                         "native_receipt_unchanged": True, "selected_view_saved_separately": True,
                                         "evidence": "verification/mcp-kline-selection.json"},
                    local_analysis={"series": ["moving_average", "momentum", "total_return", "maximum_drawdown", "annualized_volatility", "sharpe"],
                                    "risk": {"standalone": ["maximum_drawdown", "drawdown_dates", "annualized_volatility", "sharpe", "negative_return_count", "historical_var", "historical_expected_shortfall"],
                                             "benchmark": ["beta", "correlation", "r_squared", "jensen_alpha", "tracking_error", "information_ratio"],
                                             "alignment": "strict or explicitly selected common consecutive return intervals; never join prices first",
                                             "provider_metric_equivalence_verified": False},
                                    "screen": "AND conditions on explicit snapshots or a fixed WSET/WEQS universe, resumed until all WSS batches finish",
                                    "aggregate": ["sum", "mean", "median", "min", "max", "weighted_mean"],
                                    "edb_formula": "exact-date arithmetic on saved receipts, no automatic unit or availability alignment"},
                    fundamental_question_resolution={"contract": "get_stock_fundamentals", "fields": FINANCIAL_QUESTION_FIELDS,
                        "required": ["report_date_or_annual_report_year", "statement_scope_explicit_or_required_by_field_meaning", "yuan_unit", "typed_mainland_stock"],
                        "max_date_statement_combinations": 4, "requires_all_nonstructural_text_accounted_for": True,
                        "interleaved_dimension_policy": "reject_without_guessing_cartesian_product",
                        "attributable_profit_scope": "consolidated_only; inference is recorded when scope is omitted, conflicting explicit parent scope is rejected",
                        "uses_wai_financial_ids_as_fields": False, "full_natural_language_parity": False},
                    note="Candidate SDK routes are not proof of matching semantics or account permissions.")


def analysis_plan(plan):
    if not isinstance(plan, dict):
        raise Problem("INVALID_ANALYSIS_PLAN", "analysis must be an object")
    kind = plan.get("kind")
    schemas = {"series": ({"periods_per_year"}, {"field", "window", "annual_risk_free_rate"}),
               "screen": ({"conditions"}, {"sort_by", "descending", "limit"}),
               "aggregate": ({"field", "operation"}, {"weight_field"}),
               "risk": ({"periods_per_year"}, {"field", "benchmark_receipt_id", "benchmark_field", "benchmark_request",
                                                "annual_risk_free_rate", "confidence", "alignment"})}
    if not isinstance(kind, str) or kind not in schemas:
        raise Problem("INVALID_ANALYSIS_PLAN", "kind must be series/risk/screen/aggregate")
    required, optional = schemas[kind]
    if not required <= plan.keys() or set(plan) - required - optional - {"kind"}:
        raise Problem("INVALID_ANALYSIS_PLAN", "Analysis parameter mismatch", required=sorted(required), optional=sorted(optional))
    if kind == "risk":
        risk_parameters(plan["periods_per_year"], plan.get("annual_risk_free_rate", 0),
                        plan.get("confidence", 0.95), plan.get("alignment", "strict"))
        from .analytics import field_name
        field_name(plan.get("field", "close"))
        field_name(plan.get("benchmark_field", "close"))
        if "benchmark_receipt_id" in plan and (not isinstance(plan["benchmark_receipt_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", plan["benchmark_receipt_id"])):
            raise Problem("INVALID_ANALYSIS_PLAN", "benchmark_receipt_id must be a saved Wind receipt ID")
        if "benchmark_request" in plan:
            if "benchmark_receipt_id" in plan:
                raise Problem("INVALID_ANALYSIS_PLAN", "Use benchmark_request or benchmark_receipt_id, not both")
            request = plan["benchmark_request"]
            if not isinstance(request, dict) or set(request) != {"method", "arguments", "evidence"} or not isinstance(request["evidence"], str) or not request["evidence"].strip():
                raise Problem("INVALID_ANALYSIS_PLAN", "benchmark_request requires method, arguments and field/option evidence")
            validate_risk_request(request, plan.get("benchmark_field", "close"))
        elif "benchmark_receipt_id" not in plan and plan.get("alignment", "strict") != "strict":
            raise Problem("INVALID_ANALYSIS_PLAN", "Alignment selection requires a benchmark")
    return kind


def validate_risk_request(request, field):
    if not isinstance(request["method"], str) or request["method"] not in {"wsd", "wses", "wsi"}:
        raise Problem("INVALID_ANALYSIS_PLAN", "Risk analysis requires a WSD/WSES/WSI price series")
    args = validate(request["method"], request["arguments"])
    if len(csv(args["codes"])) != 1 or field.strip().casefold() not in [value.casefold() for value in csv(args["fields"], "fields")]:
        raise Problem("INVALID_ANALYSIS_PLAN", "Risk query must contain one security and the selected price field")


def analyze_result(service, result, plan):
    kind = analysis_plan(plan)
    arguments = {k: v for k, v in plan.items() if k != "kind"}
    if kind == "risk":
        benchmark_request = arguments.pop("benchmark_request", None)
        receipts = [result["receipt_id"]]
        try:
            if benchmark_request is not None:
                benchmark = service.query(benchmark_request["method"], benchmark_request["arguments"])
                arguments["benchmark_receipt_id"] = benchmark["receipt_id"]
            if arguments.get("benchmark_receipt_id"):
                receipts.append(arguments["benchmark_receipt_id"])
            derived = risk_analysis(service.store, result["receipt_id"], **arguments)
        except Problem as error:
            error.details["completed_input_receipt_ids"] = receipts
            raise
        derived["raw_input_receipt_id"] = result["receipt_id"]
        derived["query"] = {"method": result["method"], "arguments": result["arguments"]}
        if benchmark_request is not None:
            derived["benchmark_query"] = benchmark_request
        return derived
    functions = {"series": (series_analysis, {"field": "close", "window": 20, "annual_risk_free_rate": 0}),
                 "screen": (screen_snapshot, {"sort_by": None, "descending": True, "limit": 100}),
                 "aggregate": (aggregate_snapshot, {})}
    fn, defaults = functions[kind]
    derived = fn(service.store, result["receipt_id"], **{**defaults, **arguments})
    derived["raw_input_receipt_id"] = result["receipt_id"]
    derived["query"] = {"method": result["method"], "arguments": result["arguments"]}
    return derived


def checked_tool(server_type, tool_name):
    if tool_name not in MANIFEST.get(server_type, []):
        raise Problem("UNKNOWN_ALICE_TOOL", "Unknown server_type/tool_name")


def code_only(windcode):
    if not isinstance(windcode, str) or not all(re.fullmatch(r"[A-Za-z0-9]+\.[A-Za-z0-9]+", x.strip()) for x in windcode.split(",")):
        raise Problem("SECURITY_CODE_REQUIRED", "Provide a verified Wind security code; this adapter does not guess suffixes")
    return windcode


def query_with_resolution(service, method, arguments, resolution):
    try:
        return service.query(method, arguments)
    except Problem as error:
        if resolution is not None:
            error.details["security_resolution"] = resolution
        raise


def quote_dates(service, windcode, begin=None, end=None):
    """Resolve Alice quote defaults from the security snapshot, not today's date."""
    if end is not None and begin is None:
        raise Problem("INVALID_PARAMS", "Quote end cannot be supplied without begin")
    if begin is not None:
        iso_date(begin, "begin")
    if end is not None:
        date_range(begin, end)
        return begin, end, {"mode": "explicit_dates", "effective_begin": begin, "effective_end": end}
    codes = code_only(windcode).split(",")
    if len(codes) != 1:
        raise Problem("INVALID_PARAMS", "Minute quotes require one security per request")
    snapshot = service.query("wsq", {"codes": windcode, "fields": "rt_date"})
    value = snapshot["raw"]["Data"][0][0]
    if type(value) in {int, float} and math.isfinite(value) and value == int(value):
        text = str(int(value))
    elif isinstance(value, str):
        text = value
    else:
        text = ""
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        latest = iso_date(text, "RT_DATE").isoformat()
    except Problem:
        raise Problem("LATEST_TRADING_DATE_UNAVAILABLE", "Wind snapshot did not return a valid RT_DATE; supply explicit dates",
                      receipt_id=snapshot["receipt_id"], returned_value=value) from None
    effective_begin = begin or latest
    date_range(effective_begin, latest)
    return effective_begin, latest, {"mode": "latest_snapshot_trading_date", "effective_begin": effective_begin,
                                     "effective_end": latest, "snapshot_receipt_id": snapshot["receipt_id"],
                                     "snapshot_fetched_at": snapshot["fetched_at"], "field": "RT_DATE",
                                     "note": "Date reported by this security's current snapshot; not a calendar-day or completed-session assumption."}


def execute(service, server_type, tool_name, params, resolved_request=None):
    checked_tool(server_type, tool_name)
    if not isinstance(params, dict):
        raise Problem("INVALID_PARAMS", "params must be an object")
    if tool_name == "search_economic_indicator":
        if set(params) != {"question"}:
            raise Problem("INVALID_PARAMS", "This Alice contract only accepts question")
        return service.catalog.search(params["question"])
    if tool_name == "query_economic_indicator_data":
        if set(params) - {"question", "beginDate", "endDate", "observation"} or "question" not in params:
            raise Problem("INVALID_PARAMS", "Invalid economic query parameters")
        return service.economic(**params)
    count = params.get("count", 0)
    exclude_suspended = False
    security_resolution = None
    if isinstance(count, bool) or not isinstance(count, int):
        raise Problem("INVALID_PARAMS", "count must be an integer")
    if tool_name.endswith("_kline"):
        extra = set(params) - {"windcode", "begin_date", "end_date", "period", "count", "aftype", "issusp", "afdate"}
        if extra or not {"windcode", "begin_date", "end_date"} <= params.keys():
            raise Problem("INVALID_PARAMS", "Kline requires windcode, begin_date and end_date", extra=sorted(extra))
        suspension = params.get("issusp", "1")
        if suspension not in ("0", "1"):
            raise Problem("INVALID_PARAMS", "issusp must be 0 or 1")
        if params.get("afdate"):
            raise Problem("UNVERIFIED_MAPPING", "afdate requires a verified Wind option mapping")
        adjustment = {"0": "F", "1": "B", "2": "U"}.get(params.get("aftype", "0"))
        if adjustment is None:
            raise Problem("INVALID_PARAMS", "aftype must be 0, 1 or 2")
        period = params.get("period", "1d")
        if suspension == "0" and period != "1d":
            raise Problem("UNVERIFIED_MAPPING", "Suspension filtering for this period requires intraday or aggregate-period status semantics", period=period)
        exclude_suspended = suspension == "0"
        date_range(params["begin_date"], params["end_date"])
        args = {"fields": "open,high,low,close,volume,amt",
                "beginTime": params["begin_date"], "endTime": params["end_date"]}
        if exclude_suspended:
            args["fields"] += ",trade_status"
        if period in PERIODS:
            method = "wsd"
            args["options"] = f"Period={PERIODS[period]};PriceAdj={adjustment}"
        elif period in {"1min", "5min", "10min", "15min", "30min", "60min"}:
            method = "wsi"
            args["beginTime"] += " 00:00:00"
            args["endTime"] += " 23:59:59"
            args["options"] = f"BarSize={period[:-3]};PriceAdj={adjustment}"
        else:
            raise Problem("UNVERIFIED_MAPPING", "This Alice period needs additional aggregation/calendar validation", period=period)
        args["codes"], security_resolution = resolve_inputs(service, params["windcode"], server_type)
        result = query_with_resolution(service, method, args, security_resolution)
    elif tool_name.endswith("_quote"):
        if set(params) - {"windcode", "begin", "end", "count"} or "windcode" not in params:
            raise Problem("INVALID_PARAMS", "Quote requires windcode and optional begin, end, count")
        if params.get("end") is not None and params.get("begin") is None:
            raise Problem("INVALID_PARAMS", "Quote end cannot be supplied without begin")
        if params.get("begin") is not None:
            iso_date(params["begin"], "begin")
        if params.get("end") is not None:
            date_range(params["begin"], params["end"])
        code, security_resolution = resolve_inputs(service, params["windcode"], server_type)
        try:
            begin, end, selection = quote_dates(service, code, params.get("begin"), params.get("end"))
            result = service.query("wsi", {"codes": code, "fields": "open,high,low,close,volume,amt",
                                              "beginTime": begin + " 00:00:00", "endTime": end + " 23:59:59", "options": "BarSize=1;PriceAdj=U"})
        except Problem as error:
            if security_resolution is not None:
                error.details["security_resolution"] = security_resolution
            raise
        result["date_selection"] = selection
    elif tool_name.endswith("_price_indicators"):
        if set(params) - {"windcode", "indexes"} or "windcode" not in params:
            raise Problem("INVALID_PARAMS", "Use windcode and optional indexes")
        indexes = params.get("indexes", "最新交易日,交易时间,最新成交价,前收盘价,今日开盘价,今日最高价,今日最低价,成交量")
        if not isinstance(indexes, str):
            raise Problem("INVALID_PARAMS", "indexes must be comma-separated field names")
        fields = indexes.split(",")
        unknown = [f for f in fields if f not in PRICE_FIELDS]
        if unknown:
            raise Problem("UNVERIFIED_MAPPING", "Alice fields require an explicit Wind mapping", unmapped_fields=unknown)
        codes, security_resolution = resolve_inputs(service, params["windcode"], server_type, multiple=True)
        result = query_with_resolution(service, "wsq", {"codes": codes, "fields": ",".join(PRICE_FIELDS[f] for f in fields)}, security_resolution)
        result["requested_field_mapping"] = {f: PRICE_FIELDS[f] for f in fields}
    else:
        key = "query" if server_type == "financial_docs" else "question"
        if not isinstance(params.get(key), str) or not params[key].strip():
            raise Problem("INVALID_PARAMS", f"{key} is required")
        if set(params) - ({"query", "top_k"} if server_type == "financial_docs" else {"question"}):
            raise Problem("INVALID_PARAMS", "Unexpected parameters for this Alice contract")
        if server_type == "financial_docs":
            if type(params.get("top_k", 5)) is not int or params.get("top_k", 5) < 1:
                raise Problem("INVALID_PARAMS", "top_k must be a positive integer")
            raise Problem("DOCUMENT_SEARCH_NOT_MIGRATED", "Alice document retrieval and relevance ranking are not reproduced. Use query_wind_data only after verifying a native news/announcement query; it is not an equivalent document search.",
                          allowed_native_candidates=ROUTES[tool_name], requested_top_k=params.get("top_k", 5))
        if resolved_request is None:
            if tool_name == "get_stock_fundamentals":
                result = execute_fundamentals(service, params["question"])
                result["alice_contract"] = {"server_type": server_type, "tool_name": tool_name, "params": params}
                result["backend"] = "local_WindPy"
                return result
            raise Problem("QUERY_PLAN_REQUIRED", "Translate the question into a documented SDK query; no remote Alice inference is called",
                          allowed_methods=ROUTES[tool_name], required_plan={"method": "verified method", "arguments": "SDK argument object", "evidence": "Wind CG/manual reference proving fields and options"})
        if not isinstance(resolved_request, dict) or not resolved_request.get("evidence") or resolved_request.get("method") not in ROUTES[tool_name] or not isinstance(resolved_request.get("arguments"), dict):
            raise Problem("INVALID_QUERY_PLAN", "Plan needs field/option evidence and an allowed method", allowed_methods=ROUTES[tool_name])
        if set(resolved_request) - {"method", "arguments", "evidence", "analysis", "universe_receipt_id", "code_field"}:
            raise Problem("INVALID_QUERY_PLAN", "Unexpected query-plan keys")
        analysis = resolved_request.get("analysis")
        if analysis is not None:
            analysis_plan(analysis)
            if analysis["kind"] == "risk":
                validate_risk_request(resolved_request, analysis.get("field", "close"))
                if analysis.get("benchmark_receipt_id"):
                    price_series(service.store, analysis["benchmark_receipt_id"], analysis.get("benchmark_field", "close"))
        if "universe_receipt_id" in resolved_request:
            if tool_name not in {"search_stocks", "search_funds"} or resolved_request["method"] != "wss" or not analysis or analysis["kind"] != "screen":
                raise Problem("INVALID_QUERY_PLAN", "Universe screening requires search_stocks/search_funds, WSS and screen analysis")
            if set(resolved_request["arguments"]) != {"fields", "options"}:
                raise Problem("INVALID_QUERY_PLAN", "Universe WSS arguments must be fields/options; codes come only from the fixed universe receipt")
            plan = {**resolved_request["arguments"], **{key: value for key, value in analysis.items() if key != "kind"},
                    "universe_receipt_id": resolved_request["universe_receipt_id"], "code_field": resolved_request.get("code_field", "wind_code"),
                    "evidence": resolved_request["evidence"]}
            result = screen_universe(service, plan=plan)
        else:
            if "code_field" in resolved_request:
                raise Problem("INVALID_QUERY_PLAN", "code_field requires universe_receipt_id")
            result = service.query(resolved_request["method"], resolved_request["arguments"])
            if analysis is not None:
                result = analyze_result(service, result, analysis)
            else:
                result["processing"] = "raw_data_only"
        result["translation_evidence"] = resolved_request["evidence"]
        result["semantic_equivalence_verified"] = False
    result["alice_contract"] = {"server_type": server_type, "tool_name": tool_name, "params": params}
    result["backend"] = "local_WindPy"
    if security_resolution is not None:
        result["security_resolution"] = security_resolution
    if (count or exclude_suspended) and "raw" in result:
        result = select_series_rows(service.store, result, count, exclude_suspended=exclude_suspended)
    return result
