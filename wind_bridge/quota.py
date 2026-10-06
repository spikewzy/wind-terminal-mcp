"""Describe observed Wind quota errors without inferring limits or reset times."""
from __future__ import annotations

from .common import Problem


def documented_quota_policy():
    """General published usage window, separate from account-specific state."""
    return {
        "window_type": "rolling",
        "window_hours": 168,
        "fixed_calendar_reset": False,
        "function_limits": None,
        "account_usage": None,
        "billable_usage_unit": None,
        "quota_group_mapping": None,
        "recovery_time_computable": False,
        "account_specific_values_verified": False,
        "evidence": {
            "metadata_source_id": "wind_api_official_help",
            "document": "client_api",
            "section": "3. API数据流量的计算方法",
            "local_reference": "references/official-help/client_api.md",
            "corroborated_by_user_screenshot_on": "2026-10-04",
        },
        "note": "General documentation describes the preceding 7*24 hours and function-specific limits. This is not a live balance or a promise of recovery 168 hours after an error. Account limits, counting rules, shared quota groups and usage outside this MCP remain unknown.",
    }


def quota_details(raw, method):
    if not isinstance(raw, dict) or raw.get("ErrorCode") in (None, 0):
        return None
    data = raw.get("Data")
    if not isinstance(data, list):
        return None
    messages = [value for column in data if isinstance(column, list)
                for value in column if isinstance(value, str) and "quota exceed" in value.lower()]
    if not messages:
        return None
    return {"error_category": "quota_exceeded", "native_method": method,
            "wind_error_code": raw["ErrorCode"], "upstream_message": messages[0],
            "automatic_retry_recommended": False, "quota_scope": None,
            "remaining_quota": None, "reset_at": None,
            "quota_scope_and_reset_reported": False,
            "recovery_hint": "Preserve completed receipts and stop automatic retries or new bulk reads. Resume after Wind availability is confirmed; no automatic source switch. Quota scope and reset time were not reported by this response."}


def local_quota_observation(store, limit=100):
    # This is a recent local failure scan, not a live Wind quota endpoint.
    with store.connect() as connection:
        rows = connection.execute("SELECT id FROM receipts WHERE ok=0 ORDER BY fetched_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    skipped, found, later_success = [], None, None
    for (receipt_id,) in rows:
        try:
            receipt = store.read(receipt_id)
            detail = quota_details(receipt.get("response", {}).get("raw"), receipt["method"])
        except (Problem, OSError, ValueError, KeyError) as exc:
            skipped.append({"receipt_id": receipt_id, "error_type": type(exc).__name__})
            continue
        if detail:
            found = {**detail, "receipt_id": receipt_id, "observed_at": receipt["fetched_at"],
                     "historical_error_not_current_quota_measurement": True}
            with store.connect() as connection:
                newer = connection.execute("""SELECT id FROM receipts
                    WHERE ok=1 AND request_key=? AND fetched_at>?
                    ORDER BY fetched_at DESC, rowid DESC LIMIT ?""",
                    (store.key(receipt["method"], receipt["arguments"]), receipt["fetched_at"], limit)).fetchall()
            for (success_id,) in newer:
                try:
                    success = store.read(success_id)
                    response = success.get("response", {})
                    if (success.get("method") != receipt["method"] or success.get("arguments") != receipt["arguments"]
                            or not response.get("ok") or response.get("raw", {}).get("ErrorCode") != 0):
                        continue
                except (Problem, OSError, ValueError, KeyError) as exc:
                    skipped.append({"receipt_id": success_id, "error_type": type(exc).__name__})
                    continue
                with store.connect() as connection:
                    validation = connection.execute("SELECT status FROM validations WHERE receipt_id=?", (success_id,)).fetchone()
                later_success = {"receipt_id": success_id, "observed_at": success["fetched_at"],
                                 "native_method": success["method"], "identical_request": True,
                                 "native_error_code": 0, "data_validation_status": validation[0] if validation else None,
                                 "scope": "This same request later returned without a native error; account balance and other requests remain unknown."}
                break
            break
    return {"live_quota_checked": False, "current_remaining_quota": None,
            "current_reset_at": None, "current_quota_available": None,
            "documented_general_policy": documented_quota_policy(),
            "search_scope": "recent_failed_receipts_in_this_local_profile_only",
            "failed_receipt_search_limit": limit, "last_observed_error": found,
            "later_same_request_success": later_success,
            "unreadable_receipts": skipped,
            "note": "An old quota error does not prove the limit is still exhausted; absence of an error here does not prove remaining quota. Other profiles and Alice MCP are not inspected."}
