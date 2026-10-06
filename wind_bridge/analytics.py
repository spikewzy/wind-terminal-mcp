"""Auditable local analysis of saved Wind receipts; no arbitrary code execution."""
from __future__ import annotations

import ast
import math
import operator
import statistics

from .common import Problem, envelope, identity


def number(value, label):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise Problem("NON_NUMERIC_INPUT", f"{label} is missing or nonfinite; no implicit fill/drop")
    return float(value)


def field_name(field):
    if not isinstance(field, str) or not field.strip():
        raise Problem("INVALID_PARAMS", "field must be nonempty text")
    return field.strip().lower()


def bounded_integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise Problem("INVALID_PARAMS", f"{name} must be an integer in [{lower}, {upper}]")
    return value


def raw_receipt(store, receipt_id, methods):
    receipt = store.read(receipt_id)
    if receipt["data_source_id"] == "wind_terminal_api" and receipt["method"] == "series_row_selection":
        # Rebuild the selected view from its validated native source, so later
        # analysis cannot silently use unfiltered rows or a changed snapshot.
        from .kline import select_raw_rows
        params = receipt["arguments"]
        derived = receipt.get("response", {}).get("derived", {})
        if not receipt.get("response", {}).get("ok") or set(params) != {"raw_input_receipt_id", "count", "exclude_full_day_suspensions", "rule_version"} or type(params["rule_version"]) is not int or params["rule_version"] != 1:
            raise Problem("INVALID_ROW_SELECTION", "Invalid saved series selection")
        source = store.read(params["raw_input_receipt_id"])
        if source["method"] not in methods or source["method"] not in {"wsd", "wsi", "wses"}:
            raise Problem("WRONG_RECEIPT_TYPE", "Selection must refer directly to an allowed native series")
        source, source_raw = raw_receipt(store, source["receipt_id"], methods)
        if derived.get("raw_input_receipt_id") != source["receipt_id"] or derived.get("raw_input_sha256") != source["sha256"]:
            raise Problem("ROW_SELECTION_SOURCE_CHANGED", "Saved selection no longer matches its native receipt")
        if params["exclude_full_day_suspensions"]:
            periods = [part.partition("=")[2].strip().upper() for part in source["arguments"].get("options", "").split(";")
                       if part.partition("=")[0].strip().lower() == "period"]
            if source["method"] != "wsd" or periods != ["D"]:
                raise Problem("INVALID_ROW_SELECTION", "Suspension selection requires an explicit daily WSD source")
        view, selection = select_raw_rows(source_raw, params["count"], exclude_suspended=params["exclude_full_day_suspensions"])
        if derived.get("raw") != view or derived.get("row_selection") != selection or derived.get("arguments") != source["arguments"]:
            raise Problem("ROW_SELECTION_MISMATCH", "Saved selection differs from the native source and recorded rule")
        return receipt, view
    if receipt["data_source_id"] != "wind_terminal_api" or receipt["method"] not in methods:
        raise Problem("WRONG_RECEIPT_TYPE", "Analysis requires a Wind receipt of the expected type", expected=sorted(methods))
    raw = receipt["response"].get("raw", {})
    if not receipt["response"].get("ok") or raw.get("ErrorCode") != 0:
        raise Problem("FAILED_INPUT_RECEIPT", "Cannot analyze a failed upstream response")
    with store.connect() as con:
        validation = con.execute("SELECT status FROM validations WHERE receipt_id=?", (receipt_id,)).fetchone()
    if not validation or validation[0] != "passed":
        raise Problem("UNVALIDATED_INPUT_RECEIPT", "Raw response did not pass date/dimension validation")
    return receipt, raw


def save_derived(store, method, params, result):
    result = {**identity(), **result, "processing": "local_derived", "source": None}
    receipt = store.save(method, params, {"ok": True, "derived": result})
    return envelope(**{k: v for k, v in result.items() if k not in identity()}, derived_receipt_id=receipt["receipt_id"])


def series_analysis(store, receipt_id, field, window, periods_per_year, annual_risk_free_rate):
    receipt, raw = raw_receipt(store, receipt_id, {"wsd", "wses", "wsi"})
    if len(raw["Codes"]) != 1:
        raise Problem("ONE_SERIES_REQUIRED", "Use a one-security receipt")
    fields = [f.lower() for f in raw["Fields"]]
    field = field_name(field)
    if field not in fields:
        raise Problem("MISSING_FIELD", "Requested price field is absent", fields=fields)
    prices = [number(x, field) for x in raw["Data"][fields.index(field)]]
    if len(prices) < 3 or any(x <= 0 for x in prices):
        raise Problem("INVALID_PRICE_SERIES", "Need at least three positive finite price observations")
    bounded_integer(window, "window", 2, len(prices))
    annualization = number(periods_per_year, "periods_per_year")
    rf = number(annual_risk_free_rate, "annual_risk_free_rate")
    if not 0 < annualization <= 100000 or rf <= -1:
        raise Problem("INVALID_PARAMS", "Invalid annualization or risk-free rate")
    returns = [number(b / a - 1, "return") for a, b in zip(prices, prices[1:])]
    mean = statistics.mean(returns)
    volatility = statistics.stdev(returns)
    rf_per_period = (1 + rf) ** (1 / annualization) - 1
    peak, drawdown = prices[0], 0.0
    for price in prices:
        peak = max(peak, price)
        drawdown = min(drawdown, price / peak - 1)
    moving_average = [None] * (window - 1) + [statistics.mean(prices[i - window + 1:i + 1]) for i in range(window - 1, len(prices))]
    momentum = [None] * window + [prices[i] / prices[i - window] - 1 for i in range(window, len(prices))]
    params = {"receipt_id": receipt_id, "field": field, "window": window,
              "periods_per_year": periods_per_year, "annual_risk_free_rate": annual_risk_free_rate}
    result = {"input_receipt_ids": [receipt_id], "code": raw["Codes"][0], "date": raw["Times"],
              "moving_average": moving_average, "momentum": momentum,
              "statistics": {"total_return": prices[-1] / prices[0] - 1, "maximum_drawdown": drawdown,
                             "annualized_volatility": volatility * math.sqrt(annualization),
                             "sharpe": (mean - rf_per_period) / volatility * math.sqrt(annualization) if volatility else None},
              "conventions": {"return_unit": "fraction", "moving_average_unit": "same as input price (unit not inferred)",
                              "returns": "simple consecutive observed returns", "volatility": "sample standard deviation, ddof=1",
                              "maximum_drawdown": "negative fraction from running peak",
                              "risk_free": "annual effective rate converted to per-period", "annualization": annualization,
                              "missing_policy": "reject", "input_options": receipt["arguments"].get("options", ""),
                              "caveat": "Caller must choose sampling frequency and adjustment appropriate to the question; not a trading-strategy return series."}}
    if receipt["method"] == "series_row_selection":
        selected = receipt["response"]["derived"]
        result["raw_input_receipt_id"] = selected["raw_input_receipt_id"]
        result["conventions"]["input_options"] = selected["arguments"].get("options", "")
        result["conventions"]["input_row_selection"] = selected["row_selection"]
        result["conventions"]["selection_rebuilt_from_native_receipt"] = True
    return save_derived(store, "series_analysis", params, result)


OPS = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le, "eq": operator.eq, "ne": operator.ne}


def screen_data(raw, conditions, sort_by, descending, limit):
    """Apply a screen to a checked snapshot shape; the caller retains provenance."""
    fields = [f.lower() for f in raw["Fields"]]
    bounded_integer(limit, "limit", 1, 10000)
    if not isinstance(conditions, list) or len(conditions) > 50 or not isinstance(descending, bool):
        raise Problem("INVALID_PARAMS", "Use at most 50 conditions and a Boolean descending")
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {"field", "op", "value"}:
            raise Problem("INVALID_CONDITION", "Each condition requires field, op and value")
        if field_name(condition["field"]) not in fields or condition["op"] not in OPS:
            raise Problem("INVALID_CONDITION", "Use existing field, op gt/gte/lt/lte/eq/ne and value")
        if condition["value"] is None or not isinstance(condition["value"], (str, bool, int, float)):
            raise Problem("INVALID_CONDITION", "Comparison value must be a non-null scalar")
        if isinstance(condition["value"], (int, float)) and not isinstance(condition["value"], bool):
            number(condition["value"], "comparison value")
    sort_by = field_name(sort_by) if sort_by is not None else None
    if sort_by and sort_by not in fields:
        raise Problem("MISSING_FIELD", "Sort field is absent")
    rows, unknown = [], []
    for i, code in enumerate(raw["Codes"]):
        row = {"code": code, **{field: raw["Data"][j][i] for j, field in enumerate(fields)}}
        decisions = []
        for condition in conditions:
            value = row[field_name(condition["field"])]
            if value is None:
                decisions.append(None)
                continue
            try:
                decisions.append(OPS[condition["op"]](value, condition["value"]))
            except TypeError:
                raise Problem("TYPE_MISMATCH", "Filter values and field types do not match") from None
        if False in decisions:
            continue
        if None in decisions:
            unknown.append(code)
        else:
            rows.append(row)
    if sort_by:
        field = sort_by
        if any(row[field] is None for row in rows):
            raise Problem("MISSING_SORT_VALUES", "Cannot silently rank missing values")
        try:
            rows.sort(key=lambda row: row[field], reverse=descending)
        except TypeError:
            raise Problem("TYPE_MISMATCH", "Sort field contains incompatible types") from None
    return {"universe": raw["Codes"], "universe_count": len(raw["Codes"]),
                         "matched_count": len(rows), "rows": rows[:limit], "excluded_unknown_codes": sorted(set(unknown)),
                         "conditions_logic": "AND; false overrides unknown; unknown rows excluded explicitly"}


def screen_snapshot(store, receipt_id, conditions, sort_by, descending, limit):
    _, raw = raw_receipt(store, receipt_id, {"wss", "wsee", "wsq"})
    result = screen_data(raw, conditions, sort_by, descending, limit)
    return save_derived(store, "screen_snapshot", {"receipt_id": receipt_id, "conditions": conditions, "sort_by": sort_by, "descending": descending, "limit": limit},
                        {**result, "input_receipt_ids": [receipt_id],
                         "scope": "Only the explicit securities in the input receipt, not a full-market screen"})


def aggregate_snapshot(store, receipt_id, field, operation, weight_field=None):
    _, raw = raw_receipt(store, receipt_id, {"wss", "wsee", "wsq"})
    fields = [f.lower() for f in raw["Fields"]]
    field = field_name(field)
    if field not in fields:
        raise Problem("MISSING_FIELD", "Aggregate field is absent")
    values = [number(v, field) for v in raw["Data"][fields.index(field)]]
    if not values:
        raise Problem("NO_RESULTS", "No values to aggregate")
    if operation == "weighted_mean":
        weight_field = field_name(weight_field) if weight_field is not None else None
        if not weight_field or weight_field not in fields:
            raise Problem("MISSING_FIELD", "weighted_mean requires weight_field")
        weights = [number(v, weight_field) for v in raw["Data"][fields.index(weight_field)]]
        if any(w < 0 for w in weights) or sum(weights) == 0:
            raise Problem("INVALID_WEIGHTS", "Weights must be nonnegative with a positive sum")
        value = sum(v * w for v, w in zip(values, weights)) / sum(weights)
    else:
        reducers = {"sum": sum, "mean": statistics.mean, "median": statistics.median, "min": min, "max": max}
        if not isinstance(operation, str) or operation not in reducers:
            raise Problem("INVALID_OPERATION", "Use sum/mean/median/min/max/weighted_mean")
        if weight_field is not None:
            raise Problem("INVALID_PARAMS", "weight_field is only valid with weighted_mean")
        value = reducers[operation](values)
    value = number(value, "aggregate result")
    return save_derived(store, "aggregate_snapshot", {"receipt_id": receipt_id, "field": field, "operation": operation, "weight_field": weight_field},
                        {"input_receipt_ids": [receipt_id], "value": value, "observation_count": len(values), "codes": raw["Codes"],
                         "unit": None, "unit_note": "Same mathematical unit as input field; cross-security currency/unit consistency must be established by caller", "missing_policy": "reject"})


BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def compile_expression(expression, names):
    if not isinstance(expression, str) or not 1 <= len(expression) <= 1000:
        raise Problem("INVALID_EXPRESSION", "Use 1..1000 characters of arithmetic")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise Problem("INVALID_EXPRESSION", "Invalid arithmetic expression") from None
    if len(list(ast.walk(tree))) > 100:
        raise Problem("INVALID_EXPRESSION", "Expression too complex")

    def validate_node(node):
        if isinstance(node, ast.Expression):
            validate_node(node.body)
        elif isinstance(node, ast.Name) and node.id in names:
            return
        elif isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            number(node.value, "constant")
        elif isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            validate_node(node.left)
            validate_node(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            validate_node(node.operand)
        else:
            raise Problem("INVALID_EXPRESSION", "Only aliases, numeric constants and + - * / are allowed")

    # Inspect every node structurally; division by zero cannot hide later invalid syntax.
    validate_node(tree)

    def evaluate(node, row):
        if isinstance(node, ast.Expression):
            return evaluate(node.body, row)
        if isinstance(node, ast.Name) and node.id in names:
            return number(row[node.id], node.id)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return number(node.value, "constant")
        if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            return BINOPS[type(node.op)](evaluate(node.left, row), evaluate(node.right, row))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand, row)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise Problem("INVALID_EXPRESSION", "Only aliases, numeric constants and + - * / are allowed")

    return lambda row: evaluate(tree, row)


def combine_edb(store, inputs, expression):
    if not isinstance(inputs, dict) or not 1 <= len(inputs) <= 20 or any(not isinstance(name, str) or not name.isidentifier() or name.startswith("_") for name in inputs):
        raise Problem("INVALID_PARAMS", "Use 1..20 named input receipts")
    calc = compile_expression(expression, set(inputs))
    values, metadata, dates = {}, {}, None
    for name, receipt_id in inputs.items():
        receipt, raw = raw_receipt(store, receipt_id, {"edb"})
        if len(raw["Codes"]) != 1:
            raise Problem("ONE_SERIES_REQUIRED", "Use one EDB indicator per input receipt")
        code = raw["Codes"][0]
        values[name] = dict(zip([str(t)[:10] for t in raw["Times"]], raw["Data"][0]))
        metadata[name] = {"receipt_id": receipt_id, "code": code, "metadata": store.indicator(code),
                          "metadata_scope": "current_catalog_at_analysis", "fetched_at": receipt["fetched_at"]}
        dates = set(values[name]) if dates is None else dates & set(values[name])
    if not dates:
        raise Problem("NO_COMMON_OBSERVATIONS", "No exact common dates; mixed frequencies need a publication-aware research plan")
    output, issues = [], []
    for day in sorted(dates):
        try:
            value = number(calc({name: series[day] for name, series in values.items()}), "derived value")
        except (ZeroDivisionError, Problem) as exc:
            if isinstance(exc, Problem) and exc.code != "NON_NUMERIC_INPUT":
                raise
            value = None
            issues.append({"date": day, "code": "DIVISION_BY_ZERO" if isinstance(exc, ZeroDivisionError) else exc.code})
        output.append(value)
    return save_derived(store, "combine_edb", {"inputs": inputs, "expression": expression},
                        {"input_receipt_ids": list(inputs.values()), "input_metadata": metadata, "expression": expression,
                         "date": sorted(dates), "value": output, "issues": issues,
                         "alignment": "exact-date intersection; no fill or frequency conversion",
                         "unit": None, "unit_note": "No automatic magnitude/currency conversion; explicit formula and input units require review",
                         "point_in_time_safe": False, "publication_time": None})
