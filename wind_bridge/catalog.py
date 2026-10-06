from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .common import Problem, envelope


def norm(text):
    return re.sub(r"[^a-z0-9\u3400-\u9fff]", "", unicodedata.normalize("NFKC", text).lower())


def grams(text):
    text = norm(text)
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


class Catalog:
    def __init__(self, store):
        self.store = store

    def search(self, question, limit=20, offset=0, filters=None):
        if not isinstance(question, str) or not 1 <= len(question) <= 1000 or not norm(question):
            raise Problem("INVALID_PARAMS", "question needs 1..1000 characters including words or a code")
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 200 or offset < 0:
            raise Problem("INVALID_PARAMS", "limit must be 1..200 and offset >= 0")
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - {"unit", "freq", "source"} or any(not isinstance(v, str) or not v for v in filters.values()):
            raise Problem("INVALID_PARAMS", "filters may specify exact unit, freq and source strings")
        query = norm(question)
        code_text = unicodedata.normalize("NFKC", question).strip().upper()
        exact_code = code_text if re.fullmatch(r"[A-Z]\d{5,12}", code_text) else None
        query_grams = grams(question)
        explicit_tokens = [norm(t) for t in re.split(r"[\s,，:：;；]+", question) if norm(t)]
        scored = []
        catalog = self.store.catalog()
        for row in catalog:
            # An explicit identifier must not match digits in an unrelated name.
            if exact_code is not None and row["code"] != exact_code:
                continue
            if any(row.get(key) != value for key, value in filters.items()):
                continue
            name = norm(row["name"])
            score = 0.0
            if query == norm(row["code"]) or query == name:
                score = 1000.0
            elif query in name:
                score = 100.0 + len(query) / max(len(name), 1)
            else:
                # A local lexical index, not a claim to Wind's global semantic search.
                overlap = query_grams & grams(row["name"])
                if overlap:
                    score = len(overlap) / max(len(query_grams), 1)
                    if len(explicit_tokens) > 1 and all(t in name for t in explicit_tokens):
                        score += 50
            if score:
                scored.append((score, row))
        scored.sort(key=lambda p: (-p[0], p[1]["code"]))
        results = [{**row, "match_score": round(score, 6), "windpy_access": "not_implied_by_catalog"}
                   for score, row in scored[offset:offset + limit]]
        return envelope(metrics=results, total_matches=len(scored), catalog_size=len(catalog), filters=filters,
                        next_offset=offset + limit if offset + limit < len(scored) else None,
                        search_scope="local_imported_catalog", metadata_only=True,
                        match_mode="exact_code" if exact_code else "lexical_name",
                        requested_code=exact_code,
                        note="候选匹配不代表口径一致或有 WindPy 权限；目录未命中不代表 Wind 无此指标。")

    def resolve(self, question):
        if not isinstance(question, str) or not 1 <= len(question) <= 1000 or not norm(question):
            raise Problem("INVALID_PARAMS", "question must be a nonempty bounded string")
        pieces = [x.strip().upper() for x in question.split(",")]
        if pieces and all(re.fullmatch(r"[A-Z]\d{5,12}", p) for p in pieces):
            if len(set(pieces)) != len(pieces) or len(pieces) > 20:
                raise Problem("INVALID_PARAMS", "Use 1..20 distinct EDB codes per request")
            return pieces
        exact = [r for r in self.store.catalog() if norm(r["name"]) == norm(question)]
        if len(exact) == 1:
            return [exact[0]["code"]]
        raise Problem("INDICATOR_SELECTION_REQUIRED", "Use a confirmed indicator code; natural-language matches need scope review",
                      candidates=self.search(question, limit=10)["metrics"])

    def summary(self):
        rows = self.store.catalog()
        by = lambda field: dict(sorted(Counter(row.get(field) or "unknown" for row in rows).items()))
        return envelope(catalog_size=len(rows), search_scope="local_imported_catalog", metadata_only=True,
                        by_frequency=by("freq"), by_unit=by("unit"), by_upstream_source=by("source"),
                        by_metadata_source=dict(Counter(row["metadata_provenance"]["metadata_source_id"] for row in rows)),
                        full_wind_catalog=False, entitlement_verified=False,
                        automatic_seed_import=False, catalog_is_query_whitelist=False,
                        terminal_discovery={"tool": "search_economic_indicator", "scope": "terminal"},
                        note="These counts describe catalog metadata, not dataset availability or historical coverage in this terminal account.")
