"""Compare explicitly scoped WSS cells with reviewed issuer-disclosure cells.

These are sample correspondences, not a general Wind parameter dictionary.
Neither the native values nor the source filing are rewritten by this module.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from .common import ROOT, Problem
from .requests import csv, validate
from .statement_fields import STATEMENT_FIELDS

REFERENCE = "references/financial-statement-evidence.json"
FIELDS = set(STATEMENT_FIELDS)
TOLERANCE = Decimal("0.005")


def report_options(options):
    """Only the three explicit, unambiguous options used by the saved probes."""
    if not isinstance(options, str):
        return None
    parsed = {}
    for part in options.split(";"):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        key, value = key.strip().lower(), value.strip()
        if not sep or not value or key in parsed:
            return None
        parsed[key] = value
    if set(parsed) != {"rptdate", "rpttype", "unit"}:
        return None
    value = parsed["rptdate"]
    if re.fullmatch(r"\d{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        parsed["rptdate"] = dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None
    return parsed


class StatementEvidence:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.data, self.documents, self.rows = {}, {}, {}
        self.issue, self.sha256 = None, None
        try:
            payload = (self.root / REFERENCE).read_bytes()
            self.sha256 = hashlib.sha256(payload).hexdigest()
            data = json.loads(payload)
            if (data["schema_version"] != 1 or data["data_source_id"] != "wind_terminal_api"
                    or data["metadata_source_id"] != "issuer_disclosure"):
                raise ValueError("unsupported_reference_schema")
            for document in data["documents"]:
                path = (self.root / document["path"]).resolve()
                if not path.is_relative_to((self.root / "references/issuer-filings").resolve()):
                    raise ValueError("document_path_outside_reference_directory")
                if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
                    raise ValueError("document_fingerprint_changed")
                if document["document_id"] in self.documents:
                    raise ValueError("duplicate_reference_document")
                self.documents[document["document_id"]] = document
            for row in data["rows"]:
                key = tuple(row[name] for name in ("code", "report_date", "rpt_type", "unit_option", "field"))
                document = self.documents[row["document_id"]]
                if (key in self.rows or row["field"] not in FIELDS
                        or row["rpt_type"] not in STATEMENT_FIELDS[row["field"]]["report_types"]
                        or row["statement_line"] != STATEMENT_FIELDS[row["field"]]["label"]
                        or row["currency"] != "CNY" or row["unit"] != "元"
                        or not Decimal(row["value_decimal"]).is_finite()
                        or row["page"] not in document["visually_reviewed_pages"]
                        or row["header_page"] not in document["visually_reviewed_pages"]):
                    raise ValueError("invalid_or_duplicate_reference_cell")
                for name in ("statement", "statement_line", "year_column", "time_basis", "observation_note"):
                    if not isinstance(row[name], str) or not row[name]:
                        raise ValueError("incomplete_reference_cell")
                self.rows[key] = row
            if not self.rows or not isinstance(data["native_samples"], list):
                raise ValueError("incomplete_reference")
            self.data = data
        except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
            self.issue = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
            self.data, self.documents, self.rows = {}, {}, {}

    def compare(self, method, arguments, raw):
        if method != "wss":
            return None
        try:
            requested_fields = [value.lower() for value in csv(arguments.get("fields", ""), "fields")]
        except Problem:
            return None
        if not FIELDS.intersection(requested_fields):
            return None
        result = {"metadata_source_id": "issuer_disclosure", "reference": REFERENCE,
                  "reference_sha256": self.sha256, "status": "not_covered", "cells": [],
                  "comparison_scope": "whole_raw_snapshot_before_pagination",
                  "matched_cell_count": 0, "covered_cell_count": 0,
                  "all_raw_cells_covered": False, "all_covered_cells_match": None,
                  "absolute_tolerance_yuan": str(TOLERANCE), "raw_values_unchanged": True,
                  "all_parameter_semantics_certified": False, "unit_scaling_rule_certified": False,
                  "point_in_time_safe": False}
        if self.issue:
            return {**result, "status": "reference_unavailable", "issue": self.issue}
        options = report_options(arguments.get("options", ""))
        if options is None:
            return {**result, "reason": "explicit_unambiguous_rptDate_rptType_unit_only_required"}
        result["request_parameters"] = options
        codes = [str(code).upper() for code in raw.get("Codes", [])]
        fields = [str(field).lower() for field in raw.get("Fields", [])]
        columns = raw.get("Data", [])
        try:
            expected_codes = [code.upper() for code in csv(arguments["codes"])]
        except (Problem, KeyError):
            expected_codes = None
        if (raw.get("ErrorCode") != 0 or codes != expected_codes or fields != requested_fields
                or len(set(codes)) != len(codes) or len(set(fields)) != len(fields)
                or len(columns) != len(fields)
                or any(not isinstance(column, list) or len(column) != len(codes) for column in columns)):
            return {**result, "status": "native_response_not_comparable"}
        result["raw_cell_count"] = len(codes) * len(fields)
        for field_index, field in enumerate(fields):
            for code_index, code in enumerate(codes):
                row = self.rows.get((code, options["rptdate"], options["rpttype"], options["unit"], field))
                if row is None:
                    continue
                document = self.documents[row["document_id"]]
                value = columns[field_index][code_index]
                difference = None
                if type(value) in (int, float):
                    numeric = Decimal(str(value))
                    if numeric.is_finite():
                        difference = numeric - Decimal(row["value_decimal"])
                matches = difference is not None and abs(difference) <= TOLERANCE
                result["cells"].append({"code": code, "field": field, "wind_value": value,
                                        "matches_reference": matches,
                                        "status": "matched" if matches else "value_unavailable" if difference is None else "different",
                                        "difference_decimal": str(difference) if difference is not None else None,
                                        "reference": {**row, "document": document}})
        covered = len(result["cells"])
        matched = sum(cell["matches_reference"] for cell in result["cells"])
        result.update(status="compared" if covered else "not_covered",
                      covered_cell_count=covered, matched_cell_count=matched,
                      all_raw_cells_covered=covered > 0 and covered == result["raw_cell_count"],
                      all_covered_cells_match=matched == covered if covered else None)
        return result

    def catalog(self, store):
        # Read native receipts directly; Store.page adds comparison annotations.
        from .analytics import raw_receipt
        fields, issues = {}, []
        for sample in self.data.get("native_samples", []):
            try:
                receipt, raw = raw_receipt(store, sample["receipt_id"], {"wss"})
                if receipt["sha256"] != sample["native_sha256"]:
                    raise Problem("STATEMENT_SAMPLE_CHANGED", "Native sample fingerprint changed")
                if receipt["arguments"] != validate("wss", sample["arguments"]):
                    raise Problem("STATEMENT_REQUEST_CHANGED", "Native sample request changed")
                compared = self.compare("wss", receipt["arguments"], raw)
                if not compared or compared["status"] != "compared":
                    raise Problem("STATEMENT_SAMPLE_NOT_COVERED", "Sample is outside reviewed scope")
                for cell in compared["cells"]:
                    fields.setdefault(cell["field"], []).append({**cell, "method": "wss",
                        "metadata_source_id": "issuer_disclosure", "receipt_id": receipt["receipt_id"],
                        "native_sha256": receipt["sha256"], "fetched_at": receipt["fetched_at"],
                        "arguments": receipt["arguments"], "native_receipt_verified": True,
                        "reference_sha256": self.sha256, "point_in_time_safe": False})
            except (Problem, OSError, KeyError, TypeError, ValueError) as exc:
                issues.append({"receipt_id": sample.get("receipt_id") if isinstance(sample, dict) else None,
                               "issue": exc.code if isinstance(exc, Problem) else type(exc).__name__})
        return fields, {"status": "reference_unavailable" if self.issue else "checked",
                        "issue": self.issue, "sample_issues": issues, "reference": REFERENCE,
                        "reference_sha256": self.sha256, "current_entitlement_checked": False}


def statement_reference_checks(method, arguments, raw, root=ROOT):
    if method != "wss":
        return None
    return StatementEvidence(root).compare(method, arguments, raw)
