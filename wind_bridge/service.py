from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .backend import Backend
from .catalog import Catalog
from .common import Problem, date_range, envelope, identity, now
from .entities import decode_fer, recognize
from .edb_observations import observe_edb_series
from .financial_evidence import statement_reference_checks
from .requests import METHODS, csv
from .storage import Store
from .table_dates import observe_table_dates


def validate_dates(times, begin, end, *, date_only=True):
    normalized = [str(t)[:10] if date_only else str(t).replace(" ", "T") for t in times]
    # Validate actual date syntax as well as lexical bounds.
    for t in normalized:
        try:
            dt.datetime.fromisoformat(t)
        except ValueError:
            raise Problem("INVALID_RETURNED_DATE", "Wind returned an invalid date", date=t) from None
    lower, upper = (begin[:10], end[:10]) if date_only else (begin.replace(" ", "T"), end.replace(" ", "T"))
    outside = [t for t in normalized if t < lower or t > upper]
    if outside:
        raise Problem("OUT_OF_RANGE_DATA", "Wind returned dates outside the requested interval", returned_outside=outside[:10])
    if len(set(normalized)) != len(normalized):
        raise Problem("DUPLICATE_DATES", "Wind returned duplicate observation dates")
    if normalized != sorted(normalized):
        raise Problem("UNSORTED_DATES", "Wind returned dates out of order")
    return normalized


class Service:
    def __init__(self, store=None, backend=None, seed_path=None):
        self.store = store or Store()
        self.backend = backend or Backend(self.store)
        self.catalog = Catalog(self.store)
        # A research project's sample catalog is not the general service catalog.
        # Keep existing user metadata; only import a seed when explicitly supplied.
        if seed_path is not None:
            self._seed(Path(seed_path))

    def _seed(self, path):
        seed = json.loads(path.read_text(encoding="utf-8"))
        known = {r["code"] for r in self.store.catalog()}
        for row in seed["indicators"]:
            if row["meta"]["code"] not in known:
                self.store.put_indicator(row["meta"], row["provenance"])

    def discover_economic(self, question, limit=20, offset=0, filters=None,
                          scope="local", max_age_seconds=0):
        if scope not in {"local", "terminal"}:
            raise Problem("INVALID_PARAMS", "scope must be local or terminal")
        # Reuse the same input/page validation before any native query.
        local = self.catalog.search(question, limit, offset, filters)
        if scope == "local":
            return {**local, "terminal_discovery": {"tool": "search_economic_indicator", "scope": "terminal"},
                    "catalog_is_query_whitelist": False}
        if filters:
            raise Problem("METADATA_FILTERS_UNAVAILABLE", "Terminal entity recognition does not return verified unit, frequency or source filters; use local scope for those filters")
        recognized = recognize(self, question, max_age_seconds)
        candidates = recognized["edb_candidates"]
        metrics = [{"code": item["code"], "name": item["recognized_name"],
                    "unit": None, "freq": None, "source": None,
                    "metadata_provenance": {"metadata_source_id": "wind_wai_fer",
                                            "receipt_id": recognized["receipt_id"]},
                    "candidate": item, "windpy_access": "not_verified", "selection_verified": False}
                   for item in candidates[offset:offset + limit]]
        return envelope(metrics=metrics, total_matches=len(candidates),
                        next_offset=offset + limit if offset + limit < len(candidates) else None,
                        search_scope=recognized["recognition_scope"], metadata_only=True,
                        full_wind_catalog=False, catalog_is_query_whitelist=False,
                        terminal_recognition=recognized,
                        edb_text_coverage=recognized["edb_text_coverage"],
                        receipt_id=recognized["receipt_id"], selection_required=True,
                        note="Wind WAI candidates are independent of local catalog contents. This is entity recognition, not exhaustive catalog search. Missing units, frequency, publisher and history are not inferred; no candidates are selected, registered or fetched automatically.")

    def query(self, method, arguments, max_age_seconds=0):
        receipt = self.backend.request(method, arguments, max_age_seconds)
        raw = receipt["response"]["raw"]
        edb_observations = ({"edb_series_observations": observe_edb_series(raw, receipt["arguments"])}
                            if method == "edb" else {})
        checks = []
        field_aliases = []
        try:
            if method in {"edb", "wsd", "wses", "wsi", "wst", "wss", "wsee", "wsq"}:
                expected_codes = [c.upper() for c in csv(arguments["codes"])]
                actual_codes = [str(c).upper() for c in raw.get("Codes") or []]
                if actual_codes != expected_codes:
                    raise Problem("CODE_MISMATCH", "Wind returned different codes or code order", expected=expected_codes, actual=actual_codes)
                if method != "edb":
                    expected_fields = [c.upper() for c in csv(arguments["fields"], "fields")]
                    actual_fields = [str(c).upper() for c in raw.get("Fields") or []]
                    # WSI has been observed to return amount for the requested amt
                    # on an equity, ETF and index. Preserve the raw field names.
                    aliases = {"AMOUNT": "AMT"} if method == "wsi" else {}
                    expected_canonical = [aliases.get(c, c) for c in expected_fields]
                    actual_canonical = [aliases.get(c, c) for c in actual_fields]
                    if actual_canonical != expected_canonical:
                        raise Problem("FIELD_MISMATCH", "Wind returned different fields or field order", expected=expected_fields, actual=actual_fields)
                    field_aliases = [{"requested": expected, "returned": actual,
                                      "evidence": "verification/intraday-field-aliases.json"}
                                     for expected, actual in zip(expected_fields, actual_fields) if expected != actual]
                checks.append("returned_identifiers")
            if method in {"edb", "wsd", "wses", "wsi", "wst"}:
                validate_dates(raw.get("Times") or [], arguments["beginTime"], arguments["endTime"],
                               date_only=method not in {"wsi", "wst"})
                if any(not isinstance(col, list) or len(col) != len(raw["Times"]) for col in raw.get("Data", [])):
                    raise Problem("SHAPE_MISMATCH", "Wind time and value dimensions differ")
                expected_columns = len(expected_codes) if method == "edb" or len(expected_codes) > 1 else len(expected_fields)
                if len(raw.get("Data", [])) != expected_columns:
                    raise Problem("SHAPE_MISMATCH", "Wind series column count differs")
                checks.extend(["dates_within_requested_interval", "time_value_lengths"])
            if method in {"wss", "wsee", "wsq"}:
                if len(raw.get("Data", [])) != len(raw.get("Fields", [])):
                    raise Problem("SHAPE_MISMATCH", "Wind field dimension differs")
                if any(len(col) != len(raw.get("Codes", [])) for col in raw.get("Data", [])):
                    raise Problem("SHAPE_MISMATCH", "Wind security dimension differs")
                checks.append("field_security_dimensions")
            if method in {"wset", "weqs", "htocode", "wnd", "wnq", "wnc", "wai", "wsed"}:
                columns, fields = raw.get("Data") or [], raw.get("Fields") or []
                if len(columns) != len(fields) or any(not isinstance(col, list) for col in columns):
                    raise Problem("SHAPE_MISMATCH", "Wind table field and data dimensions differ")
                if len({len(col) for col in columns}) > 1:
                    raise Problem("SHAPE_MISMATCH", "Wind table columns have different row counts")
                if len(set(str(field).lower() for field in fields)) != len(fields):
                    raise Problem("DUPLICATE_FIELDS", "Wind returned duplicate table column names")
                checks.append("rectangular_table_dimensions")
            if method == "wai" and arguments.get("func") == "fer":
                decode_fer(raw)
                checks.append("wai_fer_application_success")
        except Problem as exc:
            exc.details.update(edb_observations)
            self.store.validation(receipt["receipt_id"], "failed", {"code": exc.code, **exc.details})
            exc.details["receipt_id"] = receipt["receipt_id"]
            raise
        table_dates = ({"table_date_observations": observe_table_dates(raw, receipt["arguments"].get("options", ""))}
                       if method == "wset" else {})
        compared = statement_reference_checks(method, receipt["arguments"], raw)
        statement_checks = {"statement_reference_checks": compared} if compared is not None else {}
        self.store.validation(receipt["receipt_id"], "passed", {"checks": checks, **table_dates, **statement_checks})
        return envelope(method=method, arguments=receipt["arguments"], raw=raw, source=None,
                        unit=None, receipt_id=receipt["receipt_id"], fetched_at=receipt["fetched_at"],
                        from_cache=receipt["from_cache"], checks=checks,
                        **edb_observations,
                        local_request_budget=receipt.get("local_request_budget"),
                        field_aliases=field_aliases, **table_dates, **statement_checks,
                        validation_scope={"identifiers": "returned_identifiers" in checks,
                                          "requested_date_bounds": "dates_within_requested_interval" in checks,
                                          "field_meanings": False, "units": False, "historical_availability": False},
                        note="Raw Wind units and publication times are not inferred. Nonfinite values are serialized as null.")

    def economic(self, question, beginDate=None, endDate=None, observation=None, max_age_seconds=0):
        if bool(beginDate) != bool(endDate) or bool(observation) == bool(beginDate):
            raise Problem("INVALID_PARAMS", "Provide beginDate+endDate or observation, exclusively")
        codes = self.catalog.resolve(question)
        n = None
        windows = {}
        if observation is not None:
            if not isinstance(observation, str) or not re.fullmatch(r"[1-9]\d{0,3}", observation):
                raise Problem("INVALID_PARAMS", "observation must be a positive integer string <= 9999")
            n = int(observation)
            end = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            for code in codes:
                meta = self.store.indicator(code) or {}
                stride = {"日": 3, "周": 14, "月": 62, "季": 184, "年": 732}.get(meta.get("freq"))
                if stride is None:
                    raise Problem("FREQUENCY_REQUIRED", "Unknown frequency: provide an explicit date range", indicator_code=code)
                begin = end - dt.timedelta(days=min(365 * 80, stride * n + 30))
                windows[code] = (begin.isoformat(), end.isoformat())
        else:
            date_range(beginDate, endDate)
            windows = {code: (beginDate, endDate) for code in codes}
        metrics, receipts, warnings = [], [], []
        for code in codes:
            begin, end = windows[code]
            args = {"codes": code, "beginTime": begin, "endTime": end, "options": ""}
            try:
                result = self.query("edb", args, max_age_seconds)
                raw = result["raw"]
                if [str(c).upper() for c in raw.get("Codes", [])] != [code] or len(raw.get("Data", [])) != 1:
                    raise Problem("SHAPE_MISMATCH", "Returned EDB code or value dimensions differ", requested_code=code, receipt_id=result["receipt_id"])
                times = [str(t)[:10] for t in raw["Times"]]
                values = raw["Data"][0]
                if n:
                    times, values = times[-n:], values[-n:]
                    if len(times) < n:
                        warnings.append({"code": code, "type": "INSUFFICIENT_OBSERVATIONS", "requested": n, "returned": len(times), "queried_range": [begin, end]})
                meta = self.store.indicator(code) or {"code": code, "name": None, "unit": None, "source": None, "freq": None, "metadata_provenance": {"metadata_source_id": None, "evidence": None}}
                metric = {"meta": meta, "date": times, "value": values, **identity(),
                          "source": meta.get("source"), "source_evidence": meta.get("metadata_provenance"),
                          "receipt_id": result["receipt_id"], "fetched_at": result["fetched_at"],
                          "publication_time": None, "historical_vintage": "not_provided_by_this_query",
                          "point_in_time_safe": False, "null_count": sum(v is None for v in values),
                          "query_options": "", "from_cache": result["from_cache"]}
                receipt = self.store.read(result["receipt_id"])
                self.store.save_observations(receipt, [metric])
                metrics.append(metric)
                receipts.append(result["receipt_id"])
            except Problem as exc:
                # Preserve partial work explicitly; do not fan out a failed probe.
                preflight_blocked = exc.code.startswith("LOCAL_BUDGET_")
                exc.details.update(completed_metrics=metrics, completed_receipt_ids=receipts,
                                   current_code=code,
                                   stopped_before_codes=codes[len(metrics) + (0 if preflight_blocked else 1):])
                if preflight_blocked:
                    exc.details["current_code_sent_to_wind"] = False
                raise
        return envelope(metrics=metrics, receipt_ids=receipts, warnings=warnings,
                        selection={"mode": "last_n_observation_dates_on_or_before_query_end" if n else "explicit_date_range",
                                   "requested_observations": n, "queried_ranges": windows,
                                   "publication_date_selection": False},
                        fill_policy="no_explicit_fill; returned dates are validated but not certified publication dates",
                        point_in_time_safe=False)

    def register(self, indicators, metadata_source_id, evidence):
        if not 1 <= len(indicators) <= 500:
            raise Problem("INVALID_PARAMS", "Import 1..500 metadata entries")
        provenance = {"metadata_source_id": metadata_source_id, "evidence": evidence, "imported_at": now(), "windpy_entitlement_verified": False}
        # Validate the entire batch before the first upsert.
        for row in indicators:
            if not isinstance(row, dict) or not re.fullmatch(r"[A-Z]\d{5,12}", row.get("code", "")) or not row.get("name"):
                raise Problem("INVALID_METADATA", "Every entry requires code and name")
        if not metadata_source_id or not evidence:
            raise Problem("INVALID_METADATA", "Specify metadata_source_id and evidence")
        receipt = self.store.save("catalog_import", {"indicators": indicators, **provenance}, {"ok": True})
        for row in indicators:
            self.store.put_indicator(row, {**provenance, "import_receipt_id": receipt["receipt_id"]})
        return envelope(imported=len(indicators), receipt_id=receipt["receipt_id"], data_queried=False)
