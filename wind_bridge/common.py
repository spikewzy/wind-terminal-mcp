from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path

from . import DATA_SOURCE_ID, DATA_SOURCE_NAME
from .platform_support import discover_module_dir

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("WIND_TERMINAL_MCP_DATA_DIR", ROOT / "runtime")).resolve()
MODULE_DIR = discover_module_dir()


class Problem(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details


def clean(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unexpected Wind value type: {type(value).__name__}")


def dumps(value):
    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False, sort_keys=True)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def identity():
    return {"data_source_id": DATA_SOURCE_ID, "data_source_name": DATA_SOURCE_NAME}


def envelope(**data):
    return {"ok": True, **identity(), **data}


def failure(problem):
    if isinstance(problem, Problem):
        return {"ok": False, **identity(), "code": problem.code,
                "message": str(problem), **problem.details}
    return {"ok": False, **identity(), "code": "LOCAL_ERROR", "message": str(problem)}


def iso_date(value: str, field="date"):
    if not isinstance(value, str):
        raise Problem("INVALID_PARAMS", f"{field} must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise Problem("INVALID_PARAMS", f"{field} must be YYYY-MM-DD") from None
    if value != parsed.isoformat():
        raise Problem("INVALID_PARAMS", f"{field} must be YYYY-MM-DD")
    return parsed


def date_range(begin, end):
    a, b = iso_date(begin, "beginDate"), iso_date(end, "endDate")
    if a > b:
        raise Problem("INVALID_PARAMS", "beginDate is after endDate")
    return a, b
