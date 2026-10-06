"""Find explicit sample requests, including table options, without calling Wind."""
from __future__ import annotations

import json
import re

from .catalog import norm
from .common import ROOT, Problem, envelope
from .fields import FieldCatalog
from .requests import METHODS


class QueryRecipes:
    def __init__(self, store, root=ROOT):
        self.root = root
        self.evidence = FieldCatalog(store, root)

    def search(self, question, method=None, limit=10, offset=0):
        if not isinstance(question, str) or not 1 <= len(question) <= 500 or not norm(question):
            raise Problem("INVALID_PARAMS", "question must be 1..500 nonempty characters")
        if method is not None and (not isinstance(method, str) or method not in METHODS):
            raise Problem("INVALID_PARAMS", "Unknown Wind read method", methods=sorted(METHODS))
        if type(limit) is not int or not 1 <= limit <= 30 or type(offset) is not int or offset < 0:
            raise Problem("INVALID_PARAMS", "limit must be 1..30 and offset must be nonnegative integers")
        catalog = json.loads((self.root / "references/verified-query-recipes.json").read_text(encoding="utf-8"))
        tokens = [norm(t) for t in re.split(r"[\s,，;；]+", question) if norm(t)]
        matches = []
        for recipe in catalog["recipes"]:
            if method is not None and recipe["method"] != method:
                continue
            text = json.dumps({k: recipe.get(k) for k in (
                "name", "title", "tags", "field_labels", "method", "arguments", "note")}, ensure_ascii=False)
            if not all(token in norm(text) for token in tokens):
                continue
            exact = norm(question) in {norm(recipe["name"]), norm(recipe.get("title", "")),
                                       norm(recipe["arguments"].get("tablename", ""))}
            matches.append((not exact, recipe["name"], recipe))
        matches.sort(key=lambda row: row[:2])
        results = []
        for _, _, recipe in matches[offset:offset + limit]:
            sample, counts = self.evidence._sample(recipe)
            results.append({**recipe, **sample, "value_availability": counts,
                            "successful_sample_available": sample["runtime_query_verified"] and
                            any(c["populated_count"] > 0 for c in counts.values()),
                            "parameter_semantics_certified": False, "example_is_default": False})
        return envelope(recipes=results, total_matches=len(matches),
                        next_offset=offset + limit if offset + limit < len(matches) else None,
                        search_scope="local_saved_query_examples", full_wind_catalog=False,
                        current_entitlement_checked=False, executed=False,
                        note="Original dates, codes, fields and options are examples, not defaults. Receipt checks concern this local installation. Distributed examples can remain useful when their development receipts are not present; runtime_query_verified then stays false. Successful execution does not certify units, publication times, historical completeness or every option's meaning.")
