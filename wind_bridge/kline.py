"""Select observed series rows while retaining the unmodified native receipt."""
from __future__ import annotations

from .analytics import save_derived
from .common import Problem

# Observed through WSD and checked against an issuer's suspension notice.
# Unknown and intraday suspension labels need their own evidence.
DAILY_STATUS = {"交易": "keep", "停牌一天": "exclude"}


def select_raw_rows(raw, count=0, *, exclude_suspended=False):
    if type(count) is not int or type(exclude_suspended) is not bool:
        raise Problem("INVALID_ROW_SELECTION", "Selection count must be an integer and suspension policy must be Boolean")
    eligible = list(range(len(raw["Times"])))
    excluded = []
    if exclude_suspended:
        fields = [str(field).casefold() for field in raw["Fields"]]
        if "trade_status" not in fields:
            raise Problem("TRADING_STATUS_REQUIRED", "Daily suspension filtering requires a WSD trade_status column")
        statuses = raw["Data"][fields.index("trade_status")]
        unknown = [{"date": date, "status": status} for date, status in zip(raw["Times"], statuses)
                   if not isinstance(status, str) or status not in DAILY_STATUS]
        if unknown:
            raise Problem("UNVERIFIED_TRADING_STATUS", "Trading status is missing or has no verified daily classification; no rows were filtered",
                          unknown_count=len(unknown), unknown_sample=unknown[:10],
                          verified_daily_statuses=DAILY_STATUS)
        excluded = [i for i, status in enumerate(statuses) if DAILY_STATUS[status] == "exclude"]
        eligible = [i for i, status in enumerate(statuses) if DAILY_STATUS[status] == "keep"]
    selected = eligible[:count] if count > 0 else eligible[count:] if count < 0 else eligible
    view = {**raw, "Times": [raw["Times"][i] for i in selected],
            "Data": [[column[i] for i in selected] for column in raw["Data"]]}
    selection = {"steps": (["exclude_full_day_suspensions"] if exclude_suspended else []) + ["apply_count"],
                 "input_row_count": len(raw["Times"]), "eligible_row_count": len(eligible),
                 "excluded_suspension_count": len(excluded),
                 "excluded_suspension_dates": [raw["Times"][i] for i in excluded],
                 "count": count, "returned_row_count": len(selected),
                 "price_or_volume_used_as_status": False}
    if exclude_suspended:
        selection["status_evidence"] = "verification/kline-suspension-probe.json"
        selection["verified_daily_statuses"] = dict(DAILY_STATUS)
    return view, selection


def select_series_rows(store, result, count=0, *, exclude_suspended=False):
    if not exclude_suspended and not count:
        return result
    if exclude_suspended and result["method"] != "wsd":
        raise Problem("TRADING_STATUS_REQUIRED", "Daily suspension filtering requires a WSD trade_status column",
                      receipt_id=result["receipt_id"])
    try:
        view, selection = select_raw_rows(result["raw"], count, exclude_suspended=exclude_suspended)
    except Problem as error:
        error.details["receipt_id"] = result["receipt_id"]
        raise
    derived = {**result, "raw": view, "raw_is_selected_view": True,
               "raw_input_receipt_id": result["receipt_id"], "raw_receipt_unchanged": True,
               "raw_input_sha256": store.read(result["receipt_id"])["sha256"],
               "input_receipt_ids": [result["receipt_id"]], "row_selection": selection,
               "selection_note": "receipt_id identifies the full native response; derived_receipt_id identifies this selected view. Reading the native receipt returns every original row; read the derived receipt to reproduce the selection."}
    if count:
        derived["count_selection"] = count
    return save_derived(store, "series_row_selection",
                        {"raw_input_receipt_id": result["receipt_id"], "count": count,
                         "exclude_full_day_suspensions": exclude_suspended, "rule_version": 1}, derived)
