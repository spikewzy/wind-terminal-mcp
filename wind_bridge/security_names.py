"""Resolve exact security names through Wind's typed WAI candidates.

No exchange suffixes are fabricated and no candidate is chosen by confidence.
One ambiguous member prevents every subsequent market-data request in a batch.
"""
from __future__ import annotations

import re

from .analytics import save_derived
from .common import Problem
from .entities import decode_fer

CODE = re.compile(r"[A-Za-z0-9]+\.[A-Za-z0-9]+")
DOMAIN_TYPES = {"stock_data": {"stockCN", "stockHK", "stockUS"},
                "fund_data": {"fund"}, "index_data": {"indicator"}}


def inputs(value, multiple=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 5000:
        raise Problem("INVALID_PARAMS", "windcode must contain 1..5000 characters")
    parts = [part.strip() for part in value.split(",")]
    if len(parts) > (50 if multiple else 1) or any(not part or any(ch in part for ch in "\r\n") for part in parts):
        raise Problem("INVALID_PARAMS", "Provide one security for a series, or up to 50 comma-separated securities for a snapshot")
    if len({part.casefold() for part in parts}) != len(parts):
        raise Problem("INVALID_PARAMS", "Duplicate security inputs are not allowed")
    return parts


def name_candidates(name, paragraph, domain):
    # Matching the full entity text AND its complete input span prevents a name
    # such as a fund title from being replaced by a recognized substring index.
    roots = [entity for entity in paragraph if entity["entity"].casefold() == name.casefold()
             and type(entity.get("startIndex")) is int and entity["startIndex"] == 0
             and type(entity.get("endIndex")) is int and entity["endIndex"] == len(name) - 1]
    candidates = {}
    rejected = []
    for entity in roots:
        for origin, candidate in [("recognized_entity", entity),
                                  *[("alternative_entity", item) for item in entity.get("candidateEntities", [])]]:
            code, kind = candidate.get("id"), candidate.get("type")
            if not isinstance(kind, str) or kind not in DOMAIN_TYPES[domain] or not isinstance(code, str) or not CODE.fullmatch(code):
                rejected.append({"origin": origin, "candidate": candidate,
                                 "reason": "not_a_security_code_of_the_requested_domain"})
                continue
            # Fund quote/Kline/price contracts concern exchange-traded prices,
            # whereas an OF identifier denotes an off-exchange fund series.
            if domain == "fund_data" and code.upper().endswith(".OF"):
                rejected.append({"origin": origin, "candidate": candidate,
                                 "reason": "off_exchange_fund_not_an_exchange_price_series"})
                continue
            item = candidates.setdefault(code.upper(), {"code": code.upper(), "entity_type": kind,
                "recognized_name": candidate.get("fullName") or candidate.get("entity"), "origins": []})
            item["origins"].append(origin)
    choices = list(candidates.values())
    # Do not suppress an eligible alternative simply because its confidence is
    # lower or its fullName differs (e.g. an ADR of the same company).
    return {"input": name, "status": "resolved" if len(choices) == 1 else "ambiguous" if choices else "unresolved",
            "selected_code": choices[0]["code"] if len(choices) == 1 else None,
            "candidates": choices, "rejected_candidates": rejected,
            "exact_full_span_entity_count": len(roots), "raw_entities": paragraph}


def resolve_inputs(service, value, domain, *, multiple=False):
    parts = inputs(value, multiple)
    if domain not in DOMAIN_TYPES:
        raise Problem("INVALID_PARAMS", "Security-name resolution supports stock, exchange fund and index contracts")
    names = [part for part in parts if not CODE.fullmatch(part)]
    if not names:
        return ",".join(part.upper() for part in parts), None
    result = service.query("wai", {"func": "fer", "input": "\n".join(names), "options": ""})
    paragraphs = decode_fer(result["raw"])["body"]["data"]
    receipt = service.store.read(result["receipt_id"])
    resolution = {"method": "wai", "function": "fer", "server_type": domain,
        "input_text": value, "recognition_input": "\n".join(names),
        "receipt_id": result["receipt_id"], "raw_sha256": receipt["sha256"], "fetched_at": result["fetched_at"],
        "input_receipt_ids": [result["receipt_id"]],
        "selection_rule": "one_unique_typed_code_from_all_full_span_candidates_per_name",
        "confidence_ranking_used": False, "suffix_guessed": False, "mappings": [],
        "type_evidence": "references/official-help/ai.md#返回实体类型说明",
        "live_evidence": "verification/security-name-probe.json"}
    if len(paragraphs) != len(names):
        raise Problem("SECURITY_RECOGNITION_ALIGNMENT", "Wind paragraph count differs from supplied security names; no market query was made",
                      receipt_id=result["receipt_id"], expected=len(names), actual=len(paragraphs), security_resolution=resolution)
    resolved = {name: name_candidates(name, paragraph, domain) for name, paragraph in zip(names, paragraphs)}
    resolution["mappings"] = [resolved[part] if part in resolved else
        {"input": part, "status": "explicit_code", "selected_code": part.upper(),
         "security_type_verified": False} for part in parts]
    if any(item["selected_code"] is None for item in resolution["mappings"]):
        resolution["status"] = "selection_required"
        resolution = save_derived(service.store, "security_name_resolution", {"windcode": value, "server_type": domain}, resolution)
        raise Problem("SECURITY_SELECTION_REQUIRED", "Security names are ambiguous or not recognized in this domain; choose explicit codes from the returned evidence",
                      receipt_id=result["receipt_id"], security_resolution=resolution, market_query_executed=False)
    codes = [item["selected_code"] for item in resolution["mappings"]]
    if len(set(codes)) != len(codes):
        resolution["status"] = "duplicate_selection"
        resolution = save_derived(service.store, "security_name_resolution", {"windcode": value, "server_type": domain}, resolution)
        raise Problem("DUPLICATE_SECURITY_SELECTION", "Different inputs resolve to the same security; no market query was made",
                      receipt_id=result["receipt_id"], security_resolution=resolution, market_query_executed=False)
    resolution["resolved_codes"] = codes
    resolution["status"] = "resolved"
    resolution = save_derived(service.store, "security_name_resolution", {"windcode": value, "server_type": domain}, resolution)
    return ",".join(codes), resolution
