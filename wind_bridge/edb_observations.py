"""Describe each EDB column without treating shared dates as populated observations."""
from __future__ import annotations

import math

from .table_dates import parsed_day


def observe_edb_series(raw, arguments):
    """Read-only diagnostics; never relabel, trim, fill or validate a failed query."""
    result = {"scope": "entire_raw_edb_matrix_before_pagination", "series": [],
              "series_mapping_verified": False, "dates_compared": False,
              "numeric_data_selected": False, "frequency_inferred": False,
              "unit_or_publisher_verified": False, "history_completeness_verified": False,
              "point_in_time_safe": False,
              "note": "EDB columns share a returned date axis. A date or a null cell is not a populated observation for every code. Counts describe the saved response only; query validation remains separate. No rows are removed, no values are filled, and zero values remain zero."}
    if not isinstance(raw, dict) or type(raw.get("ErrorCode")) is not int or raw["ErrorCode"] != 0:
        return {**result, "status": "upstream_not_successful"}
    if not isinstance(arguments, dict):
        return {**result, "status": "request_unavailable"}
    requested = arguments.get("codes")
    expected = requested.split(",") if isinstance(requested, str) else requested
    codes, fields, times, columns = (raw.get(key) for key in ("Codes", "Fields", "Times", "Data"))
    if (not isinstance(expected, list) or not expected or
            not all(isinstance(c, str) and c.strip() for c in expected) or
            not isinstance(codes, list) or not all(isinstance(c, str) for c in codes) or
            [c.strip().upper() for c in expected] != [c.upper() for c in codes] or
            len(set(c.upper() for c in codes)) != len(codes)):
        return {**result, "status": "code_mapping_unverified"}
    if not isinstance(fields, list) or len(fields) != 1 or str(fields[0]).casefold() != "close":
        return {**result, "status": "field_layout_unverified"}
    if (not isinstance(times, list) or not isinstance(columns, list) or len(columns) != len(codes) or
            any(not isinstance(column, list) or len(column) != len(times) for column in columns)):
        return {**result, "status": "matrix_shape_unverified"}
    result.update(series_mapping_verified=True, returned_date_count=len(times), returned_column_count=len(codes))
    begin, end = (parsed_day(arguments.get(key)) for key in ("beginTime", "endTime"))
    days = [parsed_day(value) for value in times]
    if begin is None or end is None or begin > end:
        return {**result, "status": "request_date_bounds_unverified"}
    result["requested_date_interval"] = [begin, end]
    if any(day is None for day in days) or len(set(days)) != len(days) or days != sorted(days):
        return {**result, "status": "returned_dates_unverified"}
    inside = [i for i, day in enumerate(days) if begin <= day <= end]
    outside = [i for i, day in enumerate(days) if not begin <= day <= end]
    result.update(status="observed", dates_compared=True, in_range_date_count=len(inside),
                  out_of_range_date_count=len(outside), out_of_range_dates_sample=[days[i] for i in outside[:10]],
                  populated_out_of_range_codes=[],
                  all_returned_dates_within_bounds=not outside if days else None)

    def summary(values, indices):
        nonnull = [i for i in indices if values[i] is not None]
        numeric = [i for i in nonnull if type(values[i]) is int or
                   type(values[i]) is float and math.isfinite(values[i])]
        return {"row_count": len(indices), "non_null_count": len(nonnull),
                "null_count": len(indices) - len(nonnull), "numeric_count": len(numeric),
                "non_numeric_non_null_count": len(nonnull) - len(numeric),
                "zero_count": sum(values[i] == 0 for i in numeric),
                "first_non_null_date": days[nonnull[0]] if nonnull else None,
                "last_non_null_date": days[nonnull[-1]] if nonnull else None}

    for index, (code, values) in enumerate(zip(codes, columns)):
        within, beyond = summary(values, inside), summary(values, outside)
        status = ("values_inside_and_outside_requested_interval" if within["non_null_count"] and beyond["non_null_count"] else
                  "values_only_inside_requested_interval" if within["non_null_count"] else
                  "values_only_outside_requested_interval" if beyond["non_null_count"] else
                  "no_non_null_values_in_returned_matrix")
        if beyond["non_null_count"]:
            result["populated_out_of_range_codes"].append(code)
        result["series"].append({"code": code, "column_index": index, "status": status,
                                 "in_range": within, "out_of_range": beyond,
                                 "out_of_range_non_null_dates_sample": [days[i] for i in outside if values[i] is not None][:10],
                                 "value_semantics_verified": False})
    return result
