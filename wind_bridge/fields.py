"""Search local field evidence without promoting example calls to a dictionary."""
from __future__ import annotations

import json
import re

from .analytics import raw_receipt
from .catalog import norm
from .common import ROOT, Problem, envelope
from .financial_evidence import StatementEvidence
from .requests import validate
from .statement_fields import STATEMENT_FIELDS

FIELD_METHODS = {"wss", "wsd", "wsi", "wst", "wsq", "wsee", "wses", "wsed"}


class FieldCatalog:
    def __init__(self, store, root=ROOT):
        self.store, self.root = store, root

    def _sample(self, recipe):
        sample = {key: recipe[key] for key in ("name", "title", "tags", "field_labels", "method", "arguments", "receipt_id", "evidence", "note") if key in recipe}
        sample.update(runtime_query_verified=False, field_semantics_certified=False, point_in_time_safe=False)
        counts = {}
        try:
            receipt, raw = raw_receipt(self.store, recipe["receipt_id"], {recipe["method"]})
            if receipt["arguments"] != validate(recipe["method"], recipe["arguments"]):
                raise Problem("RECIPE_RECEIPT_MISMATCH", "Saved request differs from this recipe")
            sample.update(runtime_query_verified=True, fetched_at=receipt["fetched_at"],
                          evidence_status="validated_saved_receipt")
            fields = [str(field).lower() for field in raw["Fields"]]
            for index, field in enumerate(fields):
                values = [value for column in raw["Data"] for value in column] if len(fields) == 1 else raw["Data"][index]
                populated = sum(value is not None and not (isinstance(value, str) and not value.strip()) for value in values)
                counts[field] = {"observation_count": len(values), "populated_count": populated,
                                 "missing_count": len(values) - populated, "all_values_missing": populated == 0}
        except (Problem, OSError, ValueError, KeyError) as exc:
            sample.update(evidence_status="not_verified_from_local_receipt",
                          evidence_issue=exc.code if isinstance(exc, Problem) else type(exc).__name__)
        return sample, counts

    def search(self, question, method=None, limit=20, offset=0):
        if not isinstance(question, str) or not 1 <= len(question) <= 500 or not norm(question):
            raise Problem("INVALID_PARAMS", "question must be 1..500 nonempty characters")
        if method is not None and (not isinstance(method, str) or method not in FIELD_METHODS):
            raise Problem("INVALID_PARAMS", "Use a field-based Wind method, not an EDB code or WSET table", methods=sorted(FIELD_METHODS))
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise Problem("INVALID_PARAMS", "limit must be 1..50 and offset must be nonnegative integers")
        notes = json.loads((self.root / "verification/wind-api-ui-fields.json").read_text(encoding="utf-8"))
        recipes = json.loads((self.root / "references/verified-query-recipes.json").read_text(encoding="utf-8"))
        records = {}

        def entry(field):
            return records.setdefault(field, {"field": field, "name": None, "source": None,
                                               "unit": None, "definition": None, "examples": []})

        for note in notes["fields"]:
            row = entry(note["field"].lower())
            row.update(name=note.get("name"), source=note.get("source"), unit=note.get("unit"),
                       definition={**note, "metadata_source_id": notes["metadata_source_id"],
                                   "observed_date": notes["observed_date"],
                                   "evidence": "verification/wind-api-ui-fields.json"})
        for recipe in recipes["recipes"]:
            if recipe["method"] not in FIELD_METHODS:
                continue
            sample, counts = self._sample(recipe)
            for field in recipe["arguments"].get("fields", "").split(","):
                if field.strip():
                    field = field.strip().lower()
                    returned = "amount" if recipe["method"] == "wsi" and field == "amt" else field
                    entry(field)["examples"].append({**sample, "value_availability": counts.get(returned)})

        statements, statement_status = StatementEvidence(self.root).catalog(self.store)
        tokens = [norm(token) for token in re.split(r"[\s,，;；]+", question) if norm(token)]
        matches = []
        for row in records.values():
            examples = [sample for sample in row["examples"] if method is None or sample["method"] == method]
            if method is not None and not examples:
                continue
            definition = row["definition"] or {}
            statement_evidence = statements.get(row["field"], []) if method in {None, "wss"} else []
            matched_statement = [cell for cell in statement_evidence if cell["matches_reference"]]
            statement_mapping = STATEMENT_FIELDS.get(row["field"], {}) if matched_statement else {}
            aliases = statement_mapping.get("aliases", [])
            texts = [row["field"], row["name"] or "", definition.get("meaning", ""),
                     definition.get("category", ""), *[sample["name"] for sample in examples],
                     *[sample.get("field_labels", {}).get(row["field"], "") for sample in examples],
                     *[cell["reference"]["statement_line"] for cell in matched_statement], *aliases]
            haystack = " ".join(norm(text) for text in texts)
            if not all(token in haystack for token in tokens):
                continue
            exact = norm(question) in {norm(row["field"]), norm(row["name"] or "")}
            row.update(examples=examples, methods=sorted({sample["method"] for sample in examples}),
                       official_definition_observed=bool(row["definition"]),
                       successful_query_available=any(sample["runtime_query_verified"] for sample in examples),
                       successful_sample_available=any(sample["runtime_query_verified"] and (sample.get("value_availability") or {}).get("populated_count", 0) > 0 for sample in examples),
                       statement_value_evidence=statement_evidence,
                       statement_aliases=aliases,
                       statement_mapping_note=statement_mapping.get("meaning_note"),
                       statement_report_types_with_samples=sorted({cell["reference"]["rpt_type"] for cell in matched_statement}),
                       verified_statement_sample_count=sum(cell["matches_reference"] for cell in statement_evidence),
                       parameter_semantics_certified=False, point_in_time_safe=False,
                       match_type="exact_field_or_name" if exact else "local_text_match")
            matches.append((not exact, row["field"], row))
        matches.sort(key=lambda item: item[:2])
        invalid = [item for item in recipes.get("known_invalid_candidates", [])
                   if norm(question) == norm(item["field"])]
        return envelope(fields=[row for _, _, row in matches[offset:offset + limit]],
                        total_matches=len(matches), catalog_field_count=len(records),
                        next_offset=offset + limit if offset + limit < len(matches) else None,
                        known_invalid_candidates=invalid, search_scope="local_field_notes_query_examples_and_statement_samples",
                        scope="observed", candidate_discovery={"tool": "search_wind_fields", "scope": "candidates",
                                                               "note": "Separate third-party field mappings; no automatic fallback or verification."},
                        statement_evidence_status=statement_status,
                        full_wind_catalog=False, current_entitlement_checked=False, executed=False,
                        note="Examples retain their original dates and options, not defaults. successful_query_available means the saved request passed validation; successful_sample_available additionally requires a non-null, nonblank field value. Statement evidence compares specific saved cells with reviewed issuer disclosures and does not certify other issuers, dates, option values, unit scaling or historical availability. Zero matches does not imply Wind lacks the field.")
