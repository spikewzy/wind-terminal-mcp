"""Financial entity recognition via the verified WindPy WAI fer function.

Recognition produces candidates, not a field dictionary or a resolved query.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .catalog import norm
from .bundle_metadata import FinancialCandidateContext
from .common import Problem, envelope


def decode_fer(raw):
    """Validate both SDK and application layers without inferring ID semantics."""
    if raw.get("Codes") != ["fer"] or [str(f).lower() for f in raw.get("Fields", [])] != ["details"]:
        raise Problem("WAI_RESPONSE_MISMATCH", "Expected the fer details response")
    data = raw.get("Data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], list) or len(data[0]) != 1 or not isinstance(data[0][0], str):
        raise Problem("WAI_PAYLOAD_INVALID", "Expected one JSON details cell")
    try:
        payload = json.loads(data[0][0])
    except (ValueError, TypeError):
        raise Problem("WAI_PAYLOAD_INVALID", "Wind fer details is not valid JSON") from None
    if not isinstance(payload, dict):
        raise Problem("WAI_PAYLOAD_INVALID", "Wind fer details must be an object")
    body = payload.get("body")
    status = payload.get("status")
    if type(status) not in {str, int} or str(status) != "0":
        raise Problem("WAI_APPLICATION_ERROR", "Wind fer application returned an error",
                      application_status=status, application_message=payload.get("message"))
    if not isinstance(body, dict):
        raise Problem("WAI_PAYLOAD_INVALID", "Wind fer body must be an object")
    # Current live output uses status_code; the official help uses statusCode.
    statuses = [body[key] for key in ("status_code", "statusCode") if key in body]
    if not statuses or any(type(s) is not int or s != 200 for s in statuses) or body.get("succeed") is not True:
        raise Problem("WAI_APPLICATION_ERROR", "Wind fer recognition was not successful",
                      application_status_codes=statuses, application_succeeded=body.get("succeed"),
                      application_message=body.get("message"))
    paragraphs = body.get("data")
    if not isinstance(paragraphs, list) or any(not isinstance(p, list) for p in paragraphs):
        raise Problem("WAI_PAYLOAD_INVALID", "Wind fer data must contain paragraph entity lists")
    for paragraph in paragraphs:
        for entity in paragraph:
            if not isinstance(entity, dict) or not isinstance(entity.get("entity"), str) or not isinstance(entity.get("type"), str):
                raise Problem("WAI_PAYLOAD_INVALID", "Malformed recognized entity")
            candidates = entity.get("candidateEntities", [])
            if not isinstance(candidates, list) or any(not isinstance(c, dict) for c in candidates):
                raise Problem("WAI_PAYLOAD_INVALID", "Malformed entity candidates")
    return payload


def text_span_context(paragraph, entity):
    """Verify the documented inclusive offsets before describing lexical coverage."""
    start, end = entity.get("startIndex"), entity.get("endIndex")
    context = {"span_verified": False, "text_match_scope": "unverified",
               "start_index": start, "end_index_inclusive": end,
               "matched_text": None, "unmatched_text": None,
               "lexical_coverage_only": True}
    if not isinstance(paragraph, str) or type(start) is not int or type(end) is not int or not 0 <= start <= end < len(paragraph):
        return context
    matched = paragraph[start:end + 1]
    normalized = lambda value: unicodedata.normalize("NFKC", value).casefold()
    if not isinstance(entity.get("entity"), str) or normalized(matched) != normalized(entity["entity"]):
        return context
    before, after = paragraph[:start], paragraph[end + 1:]
    return {**context, "span_verified": True,
            "text_match_scope": "full_paragraph" if not before.strip() and not after.strip() else "partial_paragraph",
            "matched_text": matched, "unmatched_text": {"before": before, "after": after}}


def edb_text_coverage(text, paragraphs, candidates):
    inputs = text.split("\n")
    aligned = len(inputs) == len(paragraphs)
    rows = []
    for index in range(len(paragraphs)):
        occurrences = [(item["code"], o) for item in candidates for o in item["occurrences"]
                       if o["paragraph_index"] == index]
        by_scope = {scope: list(dict.fromkeys(code for code, o in occurrences if o["text_match_scope"] == scope))
                    for scope in ("full_paragraph", "partial_paragraph", "unverified")}
        status = ("unaligned_paragraphs" if not aligned else
                  "full_text_candidates" if by_scope["full_paragraph"] else
                  "unverified_text_spans" if by_scope["unverified"] else
                  "partial_text_candidates_only" if by_scope["partial_paragraph"] else "no_edb_candidates")
        rows.append({"paragraph_index": index, "input_text": inputs[index] if aligned else None,
                     "candidate_codes": list(dict.fromkeys(code for code, _ in occurrences)),
                     "full_text_candidate_codes": by_scope["full_paragraph"],
                     "partial_text_candidate_codes": by_scope["partial_paragraph"],
                     "unverified_span_candidate_codes": by_scope["unverified"], "status": status,
                     "selection_verified": False})
    return {"input_paragraph_count": len(inputs), "returned_paragraph_count": len(paragraphs),
            "paragraph_counts_match": aligned, "paragraphs": rows, "lexical_coverage_only": True,
            "note": "Matching the full text does not certify the indicator definition or resolve alternative codes. Partial matches leave qualifiers outside the recognized span. Invalid/missing offsets and changed paragraph segmentation remain unverified; embedded param IDs are not promoted to EDB candidates."}


def recognize(service, text, max_age_seconds=0):
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise Problem("INVALID_PARAMS", "text must contain 1..5000 characters")
    result = service.query("wai", {"func": "fer", "input": text, "options": ""}, max_age_seconds)
    payload = decode_fer(result["raw"])
    rows = service.store.catalog()
    bundle = FinancialCandidateContext()
    entities, edb_candidates, warnings, by_code = [], [], [], {}
    inputs = text.split("\n")
    paragraphs = payload["body"]["data"]
    aligned = len(inputs) == len(paragraphs)
    for paragraph_index, paragraph in enumerate(payload["body"]["data"]):
        for entity_index, entity in enumerate(paragraph):
            # Preserve every original field, including alternative IDs and param.
            contexts = []
            for index, candidate in [(None, entity), *enumerate(entity.get("candidateEntities", []))]:
                context = bundle.match(candidate)
                if context is not None:
                    contexts.append({"origin": "recognized_entity" if index is None else "alternative_entity",
                                     "candidate_index": index, **context})
            entities.append({"paragraph_index": paragraph_index, "raw_entity": entity,
                             "windpy_field": None, "selection_verified": False,
                             "financial_candidate_context": contexts})
            span = text_span_context(inputs[paragraph_index] if aligned else None, entity)
            for candidate_index, candidate in [(None, entity), *enumerate(entity.get("candidateEntities", []))]:
                origin = "recognized_entity" if candidate_index is None else "alternative_entity"
                code = candidate.get("id")
                if candidate.get("type") != "edbIndex" or not isinstance(code, str) or not re.fullmatch(r"[A-Z]\d{5,12}", code):
                    continue
                name = candidate.get("fullName") or candidate.get("entity")
                name = name if isinstance(name, str) else None
                matches = [r for r in rows if name and norm(r["name"]) == norm(name)]
                conflicts = [r for r in matches if r["code"] != code]
                occurrence = {"paragraph_index": paragraph_index, "entity_index": entity_index,
                              "origin": origin, "candidate_index": candidate_index,
                              "recognized_name": name, "same_name_different_codes": conflicts, **span}
                if code not in by_code:
                    by_code[code] = {"code": code, "recognized_name": name, "origin": origin,
                                       "paragraph_index": paragraph_index,
                                       "local_metadata": service.store.indicator(code),
                                       "same_name_different_codes": conflicts,
                                       "selection_verified": False, "entitlement_verified": False,
                                       "occurrences": []}
                    edb_candidates.append(by_code[code])
                by_code[code]["occurrences"].append(occurrence)
                if conflicts:
                    warning = {"code": "EDB_SAME_NAME_DIFFERENT_CODES", "recognized_code": code,
                               "local_codes": [r["code"] for r in conflicts], "name": name}
                    if warning not in warnings:
                        warnings.append(warning)
    coverage = edb_text_coverage(text, paragraphs, edb_candidates)
    for row in coverage["paragraphs"]:
        if row["status"] == "partial_text_candidates_only":
            warnings.append({"code": "EDB_PARTIAL_TEXT_MATCH", "paragraph_index": row["paragraph_index"],
                             "candidate_codes": row["candidate_codes"],
                             "message": "Only part of this input paragraph matched an EDB entity; unmatched qualifiers are not resolved."})
    return envelope(entities=entities, edb_candidates=edb_candidates, warnings=warnings,
                    edb_text_coverage=coverage,
                    bundle_metadata=bundle.metadata,
                    recognition_scope="wind_wai_fer_entity_recognition", full_catalog_search=False,
                    unit_frequency_source_returned=False, selection_required=True,
                    input_text=text, receipt_id=result["receipt_id"], fetched_at=result["fetched_at"],
                    from_cache=result["from_cache"], source=None,
                    note="EDB IDs are candidates only. Same-name codes may differ in publisher or history. Numeric stockBondIndex IDs are not WindPy field names. Financial alternatives can carry local bundle context only when node ID and name both match; original entities remain unchanged. A directory match is not an API field or automatic selection. Inspect definitions before querying; no catalog entry is changed automatically.")
