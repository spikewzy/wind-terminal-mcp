"""Ground selected financial questions in typed entities and checked WSS samples.

Every meaningful span must be accounted for. This is an explicit bounded grammar,
not a claim to reproduce Alice's general natural-language inference.
"""
from __future__ import annotations

import datetime as dt
import re

from .analytics import save_derived
from .common import Problem, failure
from .entities import decode_fer
from .financial_evidence import StatementEvidence
from .requests import validate
from .security_names import CODE, DOMAIN_TYPES, name_candidates
from .statement_fields import STATEMENT_FIELDS


FIELDS = STATEMENT_FIELDS
REPORT_TYPES = {"合并报表": "1", "合并口径": "1", "合并": "1",
                "母公司报表": "2", "母公司口径": "2", "母公司": "2"}
REPORT_LABELS = {"1": "合并报表", "2": "母公司报表"}
FILLER = re.compile("|".join(sorted(["查询", "查一下", "看一下", "查看", "获取", "请", "帮我", "列出", "返回",
    "对比", "比较", "分别", "各自", "公司", "股票", "财务", "指标", "数据", "数值", "金额", "报告期", "报告日",
    "以", "按", "的", "和", "与", "及", "为"], key=len, reverse=True)))
PUNCTUATION = re.compile(r"[\s,，、;；:：。.!！?？()（）\[\]【】]+")
UNSUPPORTED = re.compile(r"营业总收入|归母|归属于|归属母公司|扣非|同比|环比|增长|TTM|单季|季度|截至|当时|最新|首次披露|原始版本|公告日|披露日|万元|亿元|千元|美元|港元|欧元|日元", re.I)


def alternatives(values):
    return re.compile("|".join(re.escape(value) for value in sorted(values, key=len, reverse=True)), re.I)


def parse_intent(question):
    if not isinstance(question, str) or not question.strip() or len(question) > 2000 or "\n" in question or "\r" in question:
        raise Problem("INVALID_PARAMS", "Financial question must be one nonempty paragraph of at most 2000 characters")
    spans = []

    def add(match, kind, value, **extra):
        if any(match.start() < row["end"] and match.end() > row["start"] for row in spans):
            return
        spans.append({"start": match.start(), "end": match.end(), "text": match.group(), "kind": kind, "value": value, **extra})

    date_patterns = [
        r"(?<!\d)((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)",
        r"(?<!\d)((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日",
        r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)",
    ]
    for pattern in date_patterns:
        for match in re.finditer(pattern, question):
            try:
                date = dt.date(*map(int, match.groups())).isoformat()
            except ValueError:
                raise Problem("INVALID_PARAMS", "Invalid financial report date", text=match.group()) from None
            add(match, "date", date, interpretation="explicit_report_date")
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})年?(?:年报|年度)", question):
        add(match, "date", match.group(1) + "-12-31", interpretation="explicit_annual_report_year")
    aliases = {alias.casefold(): field for field, row in FIELDS.items() for alias in row["aliases"]}
    for match in alternatives(aliases).finditer(question):
        add(match, "field", aliases[match.group().casefold()])
    for match in alternatives(REPORT_TYPES).finditer(question):
        add(match, "report_type", REPORT_TYPES[match.group()])
    for match in re.finditer(r"单位\s*(?:为|是)?\s*[:：]?\s*(?:人民币)?元|人民币元|以元(?:计价|为单位)", question):
        add(match, "unit", "1")
    for match in re.finditer("A股", question, re.I):
        add(match, "market", "stockCN")
    spans.sort(key=lambda row: row["start"])
    # A complete attributable-profit name consumes its ownership qualifier.
    # Unmapped qualifiers (e.g. attributable equity or deducted profit) must
    # remain unsupported, even if a shorter supported field occurs inside.
    unsupported = [match.group() for match in UNSUPPORTED.finditer(question)
                   if not any(row["kind"] == "field" and row["start"] <= match.start()
                              and row["end"] >= match.end() for row in spans)]
    if unsupported:
        raise Problem("QUERY_PLAN_REQUIRED", "This question contains semantics outside the automatic financial mapping; provide a separately verified query plan",
                      unsupported_terms=unsupported, supported_fields=[row["label"] for row in FIELDS.values()], value_query_executed=False)
    values = {kind: list(dict.fromkeys(row["value"] for row in spans if row["kind"] == kind))
              for kind in ["date", "field", "report_type", "unit", "market"]}
    scope_inference = None
    # Infer from a semantic requirement (attributable profit), never merely
    # because only one report type happens to have a successful sample.
    required_scopes = {FIELDS[field]["required_report_type"] for field in values["field"] if "required_report_type" in FIELDS[field]}
    if not values["report_type"] and len(required_scopes) == 1:
        values["report_type"] = sorted(required_scopes)
        scope_inference = {"report_type": values["report_type"][0],
            "reason": "statement_scope_required_by_field_meaning",
            "fields": [field for field in values["field"] if "required_report_type" in FIELDS[field]],
            "source": "curated_statement_mapping_checked_against_issuer_filing"}
    missing = [key for key in ["date", "field", "report_type", "unit"] if not values[key]]
    if missing:
        raise Problem("FUNDAMENTAL_PARAMETERS_REQUIRED", "Specify report date (or explicit annual-report year), supported fields, consolidated/parent statement and unit yuan",
                      missing=missing, supported_fields=[row["label"] for row in FIELDS.values()],
                      supported_report_types=list(REPORT_LABELS.values()), value_query_executed=False)
    if len(values["date"]) * len(values["report_type"]) > 4:
        raise Problem("REQUEST_TOO_LARGE", "Split the question into at most four explicit date/statement combinations", value_query_executed=False)
    conflicts = [{"field": field, "label": FIELDS[field]["label"], "requested_report_type": rpt,
                  "supported_report_types": FIELDS[field]["report_types"]}
                 for field in values["field"] for rpt in values["report_type"] if rpt not in FIELDS[field]["report_types"]]
    if conflicts:
        raise Problem("QUERY_PLAN_REQUIRED", "Requested fields and statement scopes are incompatible with the verified mapping; no report type was changed",
                      incompatible_statement_mappings=conflicts, value_query_executed=False)
    return {"question": question, "spans": spans, "report_dates": values["date"], "fields": values["field"],
            "report_types": values["report_type"], "unit_option": "1", "requested_unit": "人民币元",
            "statement_scope_inference": scope_inference,
            "explicit_market_constraint": "stockCN" if values["market"] else None}


def mapping_evidence(store, intent):
    evidence = StatementEvidence()
    by_field, status = evidence.catalog(store)
    rows, missing = [], []
    for field in intent["fields"]:
        for rpt_type in intent["report_types"]:
            samples = [row for row in by_field.get(field, []) if row["native_receipt_verified"] and row["matches_reference"]
                       and row["reference"]["rpt_type"] == rpt_type and row["reference"]["unit_option"] == "1"
                       and row["reference"]["statement_line"] == FIELDS[field]["label"]]
            if not samples:
                missing.append({"field": field, "rpt_type": rpt_type})
            else:
                rows.append({"field": field, "label": FIELDS[field]["label"], "rpt_type": rpt_type,
                             "rpt_type_label": REPORT_LABELS[rpt_type], "unit_option": "1",
                             "supporting_samples": [{"code": row["code"], "report_date": row["reference"]["report_date"],
                                 "receipt_id": row["receipt_id"], "native_sha256": row["native_sha256"],
                                 "reference_sha256": row["reference_sha256"], "pdf_sha256": row["reference"]["document"]["sha256"],
                                 "page": row["reference"]["page"]} for row in samples]})
    if missing or status["status"] != "checked":
        raise Problem("FUNDAMENTAL_MAPPING_EVIDENCE_UNAVAILABLE", "Current field/statement sample evidence could not be verified; no automatic query was made",
                      missing_mappings=missing, evidence_status=status, value_query_executed=False)
    return {"status": status, "mappings": rows, "scope": "sample_supported_mapping_not_global_parameter_or_unit_certification"}


def security_spans(question, entities, market_constraint):
    groups = {}
    for entity in entities:
        if entity["type"] not in DOMAIN_TYPES["stock_data"]:
            continue
        start, end = entity.get("startIndex"), entity.get("endIndex")
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end < len(question)
                or question[start:end + 1].casefold() != entity["entity"].casefold()):
            raise Problem("SECURITY_RECOGNITION_ALIGNMENT", "A stock entity does not match its complete reported input span")
        groups.setdefault((start, end + 1), []).append(entity)
    mappings = []
    for (start, end), roots in sorted(groups.items()):
        name = question[start:end]
        relative = [{**root, "startIndex": 0, "endIndex": len(name) - 1} for root in roots]
        resolution = name_candidates(name, relative, "stock_data")
        choices = resolution["candidates"]
        selected = None
        rule = "unique_typed_candidate"
        explicit = name.upper() if CODE.fullmatch(name) else None
        qualifier = re.match(r"\s*[（(]\s*([A-Za-z0-9]+\.[A-Za-z0-9]+)\s*[）)]", question[end:])
        qualified_span = None
        if qualifier and not explicit:
            explicit = qualifier.group(1).upper()
            qualified_span = {"start": end + qualifier.start(), "end": end + qualifier.end(),
                              "text": qualifier.group(), "kind": "security_qualifier", "value": explicit}
        if explicit:
            matched = [candidate for candidate in choices if candidate["code"] == explicit]
            if len(matched) == 1:
                selected, rule = matched[0], "explicit_code_in_verified_candidates"
            else:
                rule = "explicit_code_not_in_name_candidates"
        else:
            eligible = [candidate for candidate in choices if market_constraint is None or candidate["entity_type"] == market_constraint]
            if len(eligible) == 1:
                selected = eligible[0]
                if market_constraint:
                    rule = "unique_candidate_with_explicit_A_share_constraint"
        if selected and market_constraint and selected["entity_type"] != market_constraint:
            selected, rule = None, "explicit_code_conflicts_with_market_constraint"
        mappings.append({"start": start, "end": end, "text": name, "kind": "security",
                         "value": selected["code"] if selected else None, "selection_rule": rule,
                         "selected_entity_type": selected["entity_type"] if selected else None,
                         "candidates": choices, "rejected_candidates": resolution["rejected_candidates"],
                         "qualified_code_span": qualified_span, "raw_entities": roots})
    return mappings


def account_for_question(question, intent, securities):
    semantic_spans = intent["spans"] + [{key: row[key] for key in ["start", "end", "text", "kind", "value"]} for row in securities]
    semantic_spans.sort(key=lambda row: (row["start"], row["end"]))
    for previous, current in zip(semantic_spans, semantic_spans[1:]):
        if previous["end"] > current["start"]:
            raise Problem("QUERY_PLAN_REQUIRED", "Recognized securities and financial meaning overlap; use an explicit verified plan",
                          overlapping_spans=[previous, current], value_query_executed=False)
    # One contiguous block per dimension makes the cross product explicit enough
    # to handle multi-company comparisons without reassigning per-company dates.
    order = []
    for span in semantic_spans:
        if span["kind"] not in {"security", "date", "field", "report_type"}:
            continue
        if not order or span["kind"] != order[-1]:
            order.append(span["kind"])
    if len(order) != len(set(order)):
        raise Problem("QUERY_PLAN_REQUIRED", "Question interleaves dimensions and may assign different fields/dates to different companies; no Cartesian product was guessed",
                      dimension_order=order, value_query_executed=False)
    remaining = list(question)
    for span in semantic_spans:
        remaining[span["start"]:span["end"]] = " " * (span["end"] - span["start"])
    # Only verified parenthesized codes may be consumed when WAI omitted a
    # separate code entity; the name's original alternatives prove the binding.
    for security in securities:
        qualifier = security["qualified_code_span"]
        if qualifier and security["value"] == qualifier["value"]:
            remaining[qualifier["start"]:qualifier["end"]] = " " * (qualifier["end"] - qualifier["start"])
    for match in FILLER.finditer("".join(remaining)):
        remaining[match.start():match.end()] = " " * (match.end() - match.start())
    for match in PUNCTUATION.finditer("".join(remaining)):
        remaining[match.start():match.end()] = " " * (match.end() - match.start())
    unknown = [match.group() for match in re.finditer(r"\S+", "".join(remaining))]
    if unknown:
        raise Problem("QUERY_PLAN_REQUIRED", "Some requested content has no verified automatic mapping; it was not silently omitted",
                      unparsed_terms=unknown, value_query_executed=False)
    return {"semantic_spans": semantic_spans, "dimension_order": order, "unparsed_terms": [],
            "all_nonstructural_text_accounted_for": True, "selection_by_confidence": False}


def plan_fundamentals(service, question):
    intent = parse_intent(question)
    field_evidence = mapping_evidence(service.store, intent)
    recognized = service.query("wai", {"func": "fer", "input": question, "options": ""})
    paragraphs = decode_fer(recognized["raw"])["body"]["data"]
    native = service.store.read(recognized["receipt_id"])
    plan = {"question": question, "intent": intent, "field_evidence": field_evidence,
            "recognition_receipt_id": recognized["receipt_id"], "recognition_sha256": native["sha256"],
            "input_receipt_ids": [recognized["receipt_id"]], "security_mappings": [], "queries": [],
            "value_query_executed": False, "status": "unresolved", "rule_version": 2,
            "recognition_evidence": "verification/wai-question-spans.json",
            "full_natural_language_parity": False, "point_in_time_safe": False}
    try:
        if len(paragraphs) != 1:
            raise Problem("SECURITY_RECOGNITION_ALIGNMENT", "Expected one recognized paragraph for one financial question")
        securities = security_spans(question, paragraphs[0], intent["explicit_market_constraint"])
        plan["security_mappings"] = securities
        if not securities or any(row["value"] is None for row in securities):
            raise Problem("SECURITY_SELECTION_REQUIRED", "Choose an explicit code or an explicit market when stock candidates are ambiguous or missing", value_query_executed=False)
        if any(row["selected_entity_type"] != "stockCN" or not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)", row["value"]) for row in securities):
            raise Problem("QUERY_PLAN_REQUIRED", "Automatic statement mapping currently covers recognized mainland A shares; other markets need verified report-field/option mappings", value_query_executed=False)
        plan["text_accounting"] = account_for_question(question, intent, securities)
        codes = list(dict.fromkeys(row["value"] for row in securities))
        if len(codes) > 50:
            raise Problem("REQUEST_TOO_LARGE", "Financial snapshot supports at most 50 distinct securities")
        plan["codes"] = codes
        for date in intent["report_dates"]:
            for report_type in intent["report_types"]:
                arguments = validate("wss", {"codes": ",".join(codes), "fields": ",".join(intent["fields"]),
                    "options": f"rptDate={date.replace('-', '')};rptType={report_type};unit=1"})
                plan["queries"].append({"method": "wss", "arguments": arguments,
                                       "report_date_requested": date, "report_type_requested": report_type,
                                       "report_label_requested": REPORT_LABELS[report_type]})
        plan["status"] = "ready"
    except Problem as error:
        plan["issue"] = failure(error)
        saved = save_derived(service.store, "fundamental_query_plan", {"question": question, "rule_version": 2}, plan)
        error.details["fundamental_plan"] = saved
        error.details["value_query_executed"] = False
        raise
    return save_derived(service.store, "fundamental_query_plan", {"question": question, "rule_version": 2}, plan)


def execute_fundamentals(service, question):
    plan = plan_fundamentals(service, question)
    plan_id = plan["derived_receipt_id"]
    result = {"question": question, "plan_receipt_id": plan_id,
              "plan_sha256": service.store.read(plan_id)["sha256"], "plan": plan,
              "input_receipt_ids": [plan["recognition_receipt_id"]], "queries": [], "rows": [],
              "status": "in_progress", "unit_conversion_applied": False,
              "scope": "explicit_statement_fields_report_dates_types_and_yuan_request_for_recognized_mainland_equities",
              "semantic_equivalence_verified": False, "point_in_time_safe": False}
    for index, query in enumerate(plan["queries"]):
        try:
            response = service.query(query["method"], query["arguments"])
        except Problem as error:
            result.update(status="partial_failure", failed_query=query, failed_query_index=index,
                          error=failure(error), pending_queries=plan["queries"][index + 1:])
            saved = save_derived(service.store, "fundamental_question_result", {"plan_receipt_id": plan_id}, result)
            error.details["fundamental_workflow"] = saved
            raise
        result["queries"].append({**query, "response": response})
        result["input_receipt_ids"].append(response["receipt_id"])
        raw = response["raw"]
        checks = response.get("statement_reference_checks") or {}
        matched = {(cell["code"], cell["field"]) for cell in checks.get("cells", []) if cell["matches_reference"]}
        for code_index, code in enumerate(raw["Codes"]):
            for field_index, field in enumerate(raw["Fields"]):
                key = field.casefold()
                value = raw["Data"][field_index][code_index]
                result["rows"].append({"code": code, "field": key, "label": FIELDS[key]["label"],
                    "report_date_requested": query["report_date_requested"], "report_type_requested": query["report_type_requested"],
                    "report_label_requested": query["report_label_requested"], "value": value,
                    "requested_unit": "人民币元", "unit_option": "1",
                    "unit_verified_by_statement_sample": (code, key) in matched,
                    "unit": "元" if (code, key) in matched else None,
                    "numeric_value_available": type(value) in {int, float},
                    "value_missing": value is None or isinstance(value, str) and not value.strip(),
                    "source": response.get("source"), "receipt_id": response["receipt_id"]})
    result.update(status="complete", raw_value_count=len(result["rows"]),
                  missing_value_count=sum(row["value_missing"] for row in result["rows"]))
    return save_derived(service.store, "fundamental_question_result", {"plan_receipt_id": plan_id}, result)
