"""Search versioned community field candidates, separate from observed evidence."""
from __future__ import annotations

import hashlib
import json
import re

from .catalog import norm
from .common import ROOT, Problem, envelope

METHODS = {"wss", "wsd", "wsi", "wst", "wsq", "wsee", "wses"}


class FieldCandidates:
    def __init__(self, root=ROOT):
        self.root = root

    def search(self, question, method=None, limit=20, offset=0):
        if not isinstance(question, str) or not 1 <= len(question) <= 500 or not norm(question):
            raise Problem("INVALID_PARAMS", "question must be 1..500 nonempty characters")
        if method is not None and (not isinstance(method, str) or method not in METHODS):
            raise Problem("INVALID_PARAMS", "No community mapping for this method", methods=sorted(METHODS))
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise Problem("INVALID_PARAMS", "limit must be 1..50 and offset must be nonnegative integers")
        try:
            manifest = json.loads((self.root / "references/community-fields-manifest.json").read_text(encoding="utf-8"))
            payload = (self.root / "references/community-fields.json").read_bytes()
            if hashlib.sha256(payload).hexdigest() != manifest["catalog_sha256"]:
                raise ValueError("Catalog fingerprint differs")
            catalog = json.loads(payload)
            if catalog["schema_version"] != 1 or catalog["metadata_source_id"] != manifest["metadata_source_id"]:
                raise ValueError("Catalog identity differs")
            if len(catalog["records"]) != manifest["field_count"]:
                raise ValueError("Catalog count differs")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Problem("CANDIDATE_CATALOG_UNAVAILABLE", "Candidate catalog missing or changed; no fallback to guessed fields", issue=type(exc).__name__) from exc
        tokens = [norm(token) for token in re.split(r"[\s,，;；]+", question) if norm(token)]
        matches = []
        for record in catalog["records"]:
            references = [r for r in record["references"] if method is None or r["method"] == method]
            if not references:
                continue
            haystack = norm(" ".join([record["field"], *record["labels"], *[r["function"] for r in references]]))
            if not all(token in haystack for token in tokens):
                continue
            exact = norm(question) in {norm(record["field"]), *[norm(label) for label in record["labels"]]}
            matches.append((not exact, record["field"], {**record, "references": references,
                "methods": sorted({r["method"] for r in references}),
                "metadata_source_id": manifest["metadata_source_id"],
                "candidate_only": True, "official_definition_observed": False,
                "runtime_query_verified": False, "parameter_semantics_certified": False,
                "current_entitlement_checked": False, "options_schema": None,
                "source": None, "unit": None, "definition": None,
                "match_type": "exact_field_or_label" if exact else "community_text_match"}))
        matches.sort(key=lambda item: item[:2])
        return envelope(fields=[r for _, _, r in matches[offset:offset + limit]],
                        total_matches=len(matches), catalog_field_count=manifest["field_count"],
                        next_offset=offset + limit if offset + limit < len(matches) else None,
                        catalog_provenance=manifest, catalog_fingerprint_matches=True,
                        search_scope="community_field_candidates", scope="candidates",
                        executed=False, full_wind_catalog=False, current_entitlement_checked=False,
                        note="Candidate mappings come from third-party windget 0.0.7 released in 2022, not a current official Wind dictionary. Labels are not definitions. Its wrappers forward options without documenting required parameters; options, units and applicability remain unknown. No upstream code is imported or executed. Use observed-field/recipe evidence or a bounded native Wind query before relying on a candidate. No automatic selection, substitution or data fetch occurs.")
