"""Describe literal WSET date differences without guessing table date semantics."""
from __future__ import annotations

import datetime as dt
import re


def parsed_day(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", text):
        return None
    try:
        return dt.datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def observe_table_dates(raw, options):
    requested = []
    for item in options.split(";"):
        key, separator, value = item.partition("=")
        if separator and key.strip().casefold() == "date":
            requested.append(value.strip())
    requested_day = parsed_day(requested[0]) if len(requested) == 1 else None
    fields = []
    for field, values in zip(raw.get("Fields", []), raw.get("Data", [])):
        if str(field).casefold() not in {"date", "tradedate"} or not isinstance(values, list):
            continue
        valid, missing, invalid = [], 0, 0
        for value in values:
            day = parsed_day(value)
            if day is not None:
                valid.append(day)
            elif value is None or isinstance(value, str) and not value.strip():
                missing += 1
            else:
                invalid += 1
        unique = sorted(set(valid))
        compared = requested_day is not None and len(valid) > 0
        matches = sum(day == requested_day for day in valid) if compared else None
        fields.append({"field": field, "row_count": len(values), "parsed_date_count": len(valid),
                       "missing_count": missing, "unparsed_count": invalid,
                       "unique_dates_sample": unique[:10], "unique_date_count": len(unique),
                       "minimum": min(valid) if valid else None, "maximum": max(valid) if valid else None,
                       "matching_requested_date_count": matches,
                       "different_from_requested_date_count": len(valid) - matches if compared else None,
                       "all_rows_equal_requested_date": matches == len(values) if compared else None})
    return {"scope": "entire_raw_table_before_pagination", "date_option_values": requested,
            "unambiguous_requested_date": requested_day, "raw_metadata_times": raw.get("Times", []),
            "date_columns": fields, "date_column_semantics_certified": False,
            "requested_date_coverage_certified": False,
            "note": "Literal comparison only. A table date may mean observation, effective, publication or another date; no meaning is inferred. Times metadata is not substituted for a row date. Differing dates are preserved, not relabeled or silently discarded."}
