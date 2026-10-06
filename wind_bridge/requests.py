"""Explicit SDK read allowlist; no eval, dynamic imports, trades or WUPF."""
from __future__ import annotations

import datetime as dt
import re
from .common import Problem, date_range

# Required and optional named SDK arguments, checked against local WindPy.py.
METHODS = {
    "wsd": (["codes", "fields", "beginTime", "endTime"], ["options"]),
    "wss": (["codes", "fields"], ["options"]),
    "wsq": (["codes", "fields"], ["options"]),
    "wsi": (["codes", "fields", "beginTime", "endTime"], ["options"]),
    "wst": (["codes", "fields", "beginTime", "endTime"], ["options"]),
    "wses": (["codes", "fields", "beginTime", "endTime"], ["options"]),
    "wsee": (["codes", "fields"], ["options"]),
    "wsed": (["codes", "fields"], ["options"]),
    "wset": (["tablename"], ["options"]),
    "edb": (["codes", "beginTime", "endTime"], ["options"]),
    "htocode": (["codes", "sec_type"], ["options"]),
    "wnd": (["codes", "beginTime", "endTime"], ["options"]),
    "wnq": (["codes"], ["options"]),
    "wnc": (["id"], ["options"]),
    "wai": (["func", "input"], ["options"]),
    "weqs": (["filtername"], ["options"]),
    "tdays": (["beginTime", "endTime"], ["options"]),
    "tdaysoffset": (["offset", "beginTime"], ["options"]),
    "tdayscount": (["beginTime", "endTime"], ["options"]),
}


def csv(value, label="codes"):
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        parts = value
    elif isinstance(value, str):
        parts = value.split(",")
    else:
        raise Problem("INVALID_PARAMS", f"{label} must be comma-separated text or a string list")
    parts = [v.strip() for v in parts]
    if not parts or any(not v for v in parts):
        raise Problem("INVALID_PARAMS", f"{label} contains empty items")
    if len(set(parts)) != len(parts):
        raise Problem("INVALID_PARAMS", f"{label} contains duplicates")
    return parts


def validate(method: str, arguments: dict):
    if method not in METHODS:
        raise Problem("METHOD_NOT_ALLOWED", f"Unsupported read method {method}", allowed=list(METHODS))
    if not isinstance(arguments, dict):
        raise Problem("INVALID_PARAMS", "arguments must be an object")
    required, optional = METHODS[method]
    missing, extra = set(required) - set(arguments), set(arguments) - set(required + optional)
    if missing or extra:
        raise Problem("INVALID_PARAMS", "SDK argument mismatch", missing=sorted(missing), extra=sorted(extra))
    args = dict(arguments)
    for key, value in args.items():
        if key == "offset":
            if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 20000:
                raise Problem("INVALID_PARAMS", "offset must be an integer in [-20000, 20000]")
        elif key in {"codes", "fields", "id"}:
            parts = csv(value, key)
            if len(parts) > (50 if key != "fields" else 100):
                raise Problem("REQUEST_TOO_LARGE", f"Too many {key}; split the request")
            args[key] = ",".join(parts)
        elif not isinstance(value, str) or len(value) > 20000:
            raise Problem("INVALID_PARAMS", f"{key} must be a bounded string")
    args.setdefault("options", "")
    # Subscription callbacks and usedf are excluded. Raw dimensions are validated locally.
    if method == "wsd":
        if len(csv(args["codes"])) > 1 and len(csv(args["fields"])) > 1:
            raise Problem("INVALID_PARAMS", "Time series require one code or one field")
    if method == "wses" and len(csv(args["fields"])) != 1:
        raise Problem("INVALID_PARAMS", "WSES supports one field per request; multiple sector codes are allowed")
    if method in {"wsi", "wst"} and len(csv(args["codes"])) > 1:
        raise Problem("INVALID_PARAMS", "Use one code per intraday query")
    if method == "edb" and any(not re.fullmatch(r"[A-Z]\d{5,12}", c) for c in csv(args["codes"])):
        raise Problem("INVALID_PARAMS", "EDB requires confirmed indicator codes")
    if "endTime" in args:
        date_range(args["beginTime"][:10], args["endTime"][:10])
        if method in {"wsi", "wst"}:
            parsed = []
            for key in ("beginTime", "endTime"):
                value = args[key]
                try:
                    instant = dt.datetime.fromisoformat(value)
                except ValueError:
                    raise Problem("INVALID_PARAMS", f"{key} must be a valid local ISO date/time") from None
                if instant.tzinfo is not None or (len(value) > 10 and value[10] not in " T"):
                    raise Problem("INVALID_PARAMS", f"{key} must be a local ISO date/time without a timezone suffix")
                parsed.append(instant)
            if parsed[0] > parsed[1]:
                raise Problem("INVALID_PARAMS", "beginTime is after endTime")
    return args
