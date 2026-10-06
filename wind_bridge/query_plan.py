"""Plan bounded WSD/WSS requests locally without changing query semantics."""
from __future__ import annotations

import hashlib
import math

from .analytics import bounded_integer
from .common import Problem, dumps, envelope
from .quota import local_quota_observation
from .query_budget import estimate_cells
from .requests import csv, validate


def _parts(value, name, maximum):
    values = csv(value, name)
    if len(values) > maximum:
        raise Problem("REQUEST_TOO_LARGE", f"Planning supports at most {maximum} {name}")
    if any(len(v) > 200 or any(c in v for c in "，；\r\n") for v in values):
        raise Problem("INVALID_PARAMS", f"{name} must contain bounded identifiers with ASCII separators")
    if len({v.casefold() for v in values}) != len(values):
        raise Problem("INVALID_PARAMS", f"{name} contains case-insensitive duplicates")
    return values


def _chunks(start, stop, size):
    for i in range(start, stop, size):
        yield i, min(i + size, stop)


def plan_queries(store, method, arguments, limit=20, offset=0):
    if method not in {"wsd", "wss"}:
        raise Problem("INVALID_PARAMS", "Batch planning supports WSD and WSS only; other methods retain explicit native requests")
    if not isinstance(arguments, dict):
        raise Problem("INVALID_PARAMS", "arguments must be an object")
    bounded_integer(limit, "limit", 1, 100)
    bounded_integer(offset, "offset", 0, 10000)
    codes = _parts(arguments.get("codes"), "codes", 10000)
    fields = _parts(arguments.get("fields"), "fields", 500)
    # Validate dates, options and permitted keys before relaxing only the two
    # aggregate dimensions for planning. Every emitted request is validated too.
    base = validate(method, {**arguments, "codes": codes[0], "fields": fields[0]})
    code_count, field_count = len(codes), len(fields)
    code_batch, field_batch = 50, 100  # Existing local request limits, not Wind quota.
    code_groups = math.ceil(code_count / code_batch)
    field_groups = math.ceil(field_count / field_batch)
    candidates = []
    blocks = []
    if method == "wss":
        total = code_groups * field_groups
        selected = {"strategy": "snapshot_code_field_blocks", "request_count": total}
        blocks = [(a, b, c, d) for a, b in _chunks(0, code_count, code_batch)
                  for c, d in _chunks(0, field_count, field_batch)]
    else:
        # Compare uniform code/field loops plus a whole-code or whole-field tail.
        # These mixed templates avoid an extra request for a nearly empty tail.
        # Do not claim a global optimum over arbitrary rectangle partitions.
        candidates = [
            {"strategy": "per_code", "request_count": code_count * field_groups},
            {"strategy": "per_field", "request_count": field_count * code_groups},
        ]
        code_tail = min(range(code_count + 1), key=lambda n: (
            n * field_groups + field_count * math.ceil((code_count - n) / code_batch), n))
        field_tail = min(range(field_count + 1), key=lambda n: (
            n * code_groups + code_count * math.ceil((field_count - n) / field_batch), n))
        candidates += [
            {"strategy": "field_groups_then_code_tail", "code_tail_count": code_tail,
             "request_count": code_tail * field_groups + field_count * math.ceil((code_count - code_tail) / code_batch)},
            {"strategy": "code_groups_then_field_tail", "field_tail_count": field_tail,
             "request_count": field_tail * code_groups + code_count * math.ceil((field_count - field_tail) / field_batch)},
        ]
        selected = min(candidates, key=lambda c: c["request_count"])
        total = selected["request_count"]
        if total > 10000:
            raise Problem("PLAN_TOO_LARGE", "Selected plan exceeds 10000 requests; reduce the requested universe or fields", request_count=total)
        if selected["strategy"] in {"per_field", "field_groups_then_code_tail"}:
            boundary = code_count - selected.get("code_tail_count", 0)
            blocks = [(a, b, f, f + 1) for f in range(field_count)
                      for a, b in _chunks(0, boundary, code_batch)]
            blocks += [(c, c + 1, a, b) for c in range(boundary, code_count)
                       for a, b in _chunks(0, field_count, field_batch)]
        else:
            boundary = field_count - selected.get("field_tail_count", 0)
            blocks = [(c, c + 1, a, b) for c in range(code_count)
                      for a, b in _chunks(0, boundary, field_batch)]
            blocks += [(a, b, f, f + 1) for f in range(boundary, field_count)
                       for a, b in _chunks(0, code_count, code_batch)]
    assert len(blocks) == total
    canonical = {**base, "codes": ",".join(codes), "fields": ",".join(fields)}
    fingerprint = hashlib.sha256(dumps({"method": method, "arguments": canonical,
        "planner_version": 1, "max_codes_per_request": code_batch,
        "max_fields_per_request": field_batch}).encode()).hexdigest()
    requests = []
    for index, (c0, c1, f0, f1) in enumerate(blocks[offset:offset + limit], offset):
        args = validate(method, {**base, "codes": ",".join(codes[c0:c1]), "fields": ",".join(fields[f0:f1])})
        requests.append({"index": index, "tool": "query_wind_data", "method": method,
                         "arguments": args, "code_slice": [c0, c1], "field_slice": [f0, f1],
                         "data_volume_estimate": estimate_cells(method, args)})
    return envelope(plan_id=fingerprint, planner_version=1, method=method,
        input_shape={"codes": code_count, "fields": field_count,
                     "code_field_pairs": code_count * field_count,
                     "beginTime": base.get("beginTime"), "endTime": base.get("endTime"),
                     "time_observations": 1 if method == "wss" else None},
        selected_strategy=selected, compared_strategies=candidates,
        optimization_scope="fewest_requests_among_four_templates_under_local_dimension_limits" if method == "wsd" else "snapshot_rectangular_batches",
        globally_minimal_proven=False, total_requests=total, requests=requests,
        data_volume_estimate=estimate_cells(method, canonical),
        next_offset=offset + limit if offset + limit < total else None,
        local_request_limits={"codes": code_batch, "fields": field_batch, "not_upstream_quota": True},
        invariants={"input_order_preserved_in_slices": True, "all_code_field_pairs_covered_once": True,
                    "time_range_split": False, "options_unchanged": True,
                    "cross_method_substitution": False, "missing_values_filled": False},
        execution_policy={"automatic_execution": False, "on_quota_exceeded": "stop_and_keep_completed_receipts",
                          "automatic_retry": False, "cross_source_fallback": False},
        quota_observation=local_quota_observation(store), executed=False, native_calls=0,
        upstream_parameters_verified=False, units_certified=False, point_in_time_safe=False,
        documentation={"document": "api_faq", "questions": [8, 9, 10, 11, 12, 23, 31],
                       "local_reference": "skills/wind-terminal-api/references/query-planning.md"},
        note="Planning changes code/field grouping only. Fewer calls do not reduce the requested code-field-time cells or bypass Wind usage limits. Every page is required to cover the full input; slices are zero-based, stop-exclusive. Preserve original receipts and use returned identifiers/dates when assembling results. Financial report periods may need Days=Alldays; no calendar, rptType, Fill or showblank option is inserted. Options, raw units, native missing/zero semantics and current entitlement still require review. Existing saved receipts can be read locally; this tool does not query data or refresh quota.")
