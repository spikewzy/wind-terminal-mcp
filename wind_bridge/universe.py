"""Resumable WSS screening of a fixed, validated WSET/WEQS security universe."""
from __future__ import annotations

import re

from .analytics import bounded_integer, raw_receipt, save_derived, screen_data
from .common import Problem
from .requests import csv, validate

CHECKPOINT_METHOD = "universe_screen_checkpoint"
BATCH_SIZE = 50


def checked_plan(store, plan):
    required = {"universe_receipt_id", "fields", "options", "conditions", "evidence"}
    optional = {"code_field", "sort_by", "descending", "limit"}
    if not isinstance(plan, dict) or not required <= plan.keys() or set(plan) - required - optional:
        raise Problem("INVALID_SCREEN_PLAN", "Use universe_receipt_id, fields, options, conditions, evidence and optional code_field/sort_by/descending/limit")
    if not isinstance(plan["evidence"], str) or not plan["evidence"].strip():
        raise Problem("INVALID_SCREEN_PLAN", "Field and option evidence is required")
    receipt, raw = raw_receipt(store, plan["universe_receipt_id"], {"wset", "weqs"})
    code_field = plan.get("code_field", "wind_code")
    if not isinstance(code_field, str):
        raise Problem("INVALID_SCREEN_PLAN", "code_field must name the security-code column")
    names = [str(field).lower() for field in raw["Fields"]]
    if code_field.lower() not in names:
        raise Problem("MISSING_CODE_COLUMN", "The universe receipt has no requested security-code column", fields=names)
    original_codes = raw["Data"][names.index(code_field.lower())]
    if not original_codes or len(original_codes) > 10000:
        raise Problem("INVALID_UNIVERSE", "The universe must contain 1..10000 securities; it is never silently truncated")
    if any(not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]+\.[A-Za-z0-9]+", code) for code in original_codes):
        raise Problem("INVALID_UNIVERSE", "Every universe member must have an explicit Wind security code")
    codes = [code.upper() for code in original_codes]
    if len(set(codes)) != len(codes):
        raise Problem("DUPLICATE_UNIVERSE_CODES", "Duplicate universe members require explicit review, not automatic deduplication")
    args = validate("wss", {"codes": codes[:BATCH_SIZE], "fields": plan["fields"], "options": plan["options"]})
    fields = [field.lower() for field in csv(args["fields"], "fields")]
    if len(set(fields)) != len(fields) or "code" in fields:
        raise Problem("INVALID_SCREEN_PLAN", "Fields must be case-insensitively distinct and may not shadow code")
    normalized = {**plan, "code_field": code_field.lower(), "fields": ",".join(fields),
                  "options": args["options"], "sort_by": plan.get("sort_by"),
                  "descending": plan.get("descending", True), "limit": plan.get("limit", 100)}
    screen_data({"Codes": [], "Fields": fields, "Data": [[] for _ in fields]}, normalized["conditions"],
                normalized["sort_by"], normalized["descending"], normalized["limit"])
    return normalized, receipt, codes, fields


def screen_universe(service, plan=None, continuation_receipt_id=None, batches_per_call=2):
    bounded_integer(batches_per_call, "batches_per_call", 1, 3)
    if (plan is None) == (continuation_receipt_id is None):
        raise Problem("INVALID_SCREEN_PLAN", "Supply exactly one initial plan or continuation_receipt_id")
    child_ids = []
    expected_universe_hash = None
    if continuation_receipt_id is not None:
        checkpoint = service.store.read(continuation_receipt_id)
        if checkpoint["data_source_id"] != "wind_terminal_api" or checkpoint["method"] != CHECKPOINT_METHOD:
            raise Problem("WRONG_CHECKPOINT_TYPE", "Resume requires a Wind universe-screen checkpoint")
        state = checkpoint.get("response", {}).get("derived", {})
        if state.get("status") not in {"pending", "batch_failed"}:
            raise Problem("INVALID_CHECKPOINT", "This checkpoint is not resumable")
        plan = checkpoint["arguments"]["plan"]
        child_ids = state.get("snapshot_receipt_ids")
        expected_universe_hash = state.get("universe_sha256")
        if not isinstance(child_ids, list) or len(child_ids) != len(set(child_ids)) or not expected_universe_hash:
            raise Problem("INVALID_CHECKPOINT", "Checkpoint lacks unambiguous snapshot provenance")
    plan, universe, codes, fields = checked_plan(service.store, plan)
    if expected_universe_hash and expected_universe_hash != universe["sha256"]:
        raise Problem("UNIVERSE_CHANGED", "The fixed universe receipt changed since the checkpoint")
    combined = {"Codes": [], "Fields": fields, "Data": [[] for _ in fields]}
    fetched = []

    def arguments_at(offset):
        return {"codes": ",".join(codes[offset:offset + BATCH_SIZE]), "fields": plan["fields"], "options": plan["options"]}

    def append(receipt, raw):
        expected = arguments_at(len(combined["Codes"]))
        if not expected["codes"] or receipt["arguments"] != expected:
            raise Problem("CHECKPOINT_QUERY_MISMATCH", "Snapshot request differs from the fixed universe slice or field/options plan")
        returned = [str(code).upper() for code in raw["Codes"]]
        if returned != expected["codes"].split(",") or [str(field).lower() for field in raw["Fields"]] != fields:
            raise Problem("CHECKPOINT_QUERY_MISMATCH", "Snapshot codes or fields no longer agree with the plan")
        if len(raw["Data"]) != len(fields) or any(len(values) != len(returned) for values in raw["Data"]):
            raise Problem("SHAPE_MISMATCH", "Snapshot values do not match its security axis")
        combined["Codes"].extend(returned)
        for target, values in zip(combined["Data"], raw["Data"]):
            target.extend(values)
        fetched.append(receipt["fetched_at"])

    for receipt_id in child_ids:
        receipt, raw = raw_receipt(service.store, receipt_id, {"wss"})
        append(receipt, raw)

    def checkpoint(status, error=None):
        data = {"status": status, "screen_ready": False, "universe_receipt_id": universe["receipt_id"],
                "universe_sha256": universe["sha256"], "universe_count": len(codes),
                "scanned_count": len(combined["Codes"]), "remaining_count": len(codes) - len(combined["Codes"]),
                "snapshot_receipt_ids": child_ids, "point_in_time_safe": False, "error": error}
        saved = save_derived(service.store, CHECKPOINT_METHOD, {"plan": plan}, data)
        return {**saved, "ok": status != "batch_failed", "continuation_receipt_id": saved["derived_receipt_id"],
                "note": "No ranked rows are returned until every member in the fixed universe has been queried. Resume explicitly using the continuation receipt; no automatic retry on errors."}

    for _ in range(batches_per_call):
        if len(combined["Codes"]) == len(codes):
            break
        try:
            result = service.query("wss", arguments_at(len(combined["Codes"])))
            receipt, raw = raw_receipt(service.store, result["receipt_id"], {"wss"})
            append(receipt, raw)
            child_ids.append(result["receipt_id"])
        except Problem as exc:
            return checkpoint("batch_failed", {"code": exc.code, "message": str(exc), **exc.details})
    if len(combined["Codes"]) < len(codes):
        return checkpoint("pending")
    result = screen_data(combined, plan["conditions"], plan["sort_by"], plan["descending"], plan["limit"])
    return save_derived(service.store, "screen_universe", {"plan": plan},
                        {**result, "status": "complete", "screen_ready": True, "scanned_count": len(codes),
                         "input_receipt_ids": [universe["receipt_id"], *child_ids], "snapshot_receipt_ids": child_ids,
                         "universe_query": {"method": universe["method"], "arguments": universe["arguments"],
                                            "fetched_at": universe["fetched_at"], "sha256": universe["sha256"]},
                         "snapshot_fetched_at_range": [min(fetched), max(fetched)], "snapshot_options": plan["options"],
                         "point_in_time_safe": False, "full_market_coverage_certified": False,
                         "scope": "All members of the exact supplied WSET/WEQS universe receipt, globally sorted before limit. Universe completeness, field meanings and historical information availability require separate evidence."})
