"""Test the configured stdio MCP from an unrelated directory; --live reads Wind."""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def verify_budget_preflight(entry, record):
    """Exercise real MCP dispatch with a temporary profile and no loadable SDK."""
    from wind_bridge.storage import Store
    with tempfile.TemporaryDirectory(prefix="wind-mcp-budget-") as directory:
        temporary = Path(directory)
        profile = temporary / "profile"
        store = Store(profile)
        policy_path = profile / "query-budget.json"
        policy_path.write_text(json.dumps({"max_estimated_cells_per_request": 1}), encoding="utf-8")
        args = {"codes": "TEST00.SHF", "fields": "close", "beginTime": "2026-09-29",
                "endTime": "2026-09-29", "options": ""}
        fixture = store.save("wsd", args, {"ok": True, "verification_fixture": True,
                            "raw": {"ErrorCode": 0, "Codes": ["TEST00.SHF"], "Fields": ["CLOSE"],
                                    "Times": ["2026-09-29"], "Data": [[1]]}})
        params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=directory,
            env={**(entry.get("env") or {}), "WIND_TERMINAL_MCP_DATA_DIR": str(profile),
                 "WIND_TERMINAL_MODULE_DIR": str(temporary / "absent-wind-sdk")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=60)) as client:
                await client.initialize()
                async def call(name, arguments):
                    response = await client.call_tool(name, arguments)
                    value = json.loads(next(item.text for item in response.content if item.type == "text"))
                    if response.isError or value != response.structuredContent:
                        raise RuntimeError(f"Budget text/structured contract failed: {name}")
                    return value
                status = await call("wind_status", {"connect": False})
                record("budget_temporary_profile_no_loadable_sdk", status.get("ok") and not status["module_exists"]
                       and status["local_request_budget"]["configured"]
                       and status["local_request_budget"]["usage"]["reserved_native_calls"] == 0)
                blocked = await call("query_wind_data", {"method": "wsd", "arguments": {**args, "beginTime": "2000-01-01"}})
                record("budget_long_history_blocked_before_wind", blocked.get("code") == "LOCAL_BUDGET_EXCEEDED"
                       and blocked.get("budget") == "max_estimated_cells_per_request"
                       and blocked.get("account_quota") is False and "receipt_id" not in blocked)
                unknown = await call("recognize_wind_entities", {"text": "budget verification fixture"})
                record("budget_unknown_shape_blocks_wai_compat_path", unknown.get("code") == "LOCAL_BUDGET_UNESTIMATED"
                       and unknown.get("account_quota") is False and "receipt_id" not in unknown)
                policy_path.write_text(json.dumps({"max_native_calls": 0}), encoding="utf-8")
                connected = await call("wind_status", {"connect": True})
                record("budget_policy_reload_blocks_connection_attempt", connected.get("code") == "LOCAL_BUDGET_EXCEEDED"
                       and connected.get("budget") == "max_native_calls" and "receipt_id" not in connected)
                policy_path.write_text("not json", encoding="utf-8")
                invalid = await call("query_wind_data", {"method": "wsd", "arguments": args})
                record("budget_invalid_config_fails_closed", invalid.get("code") == "LOCAL_BUDGET_CONFIG_ERROR"
                       and "receipt_id" not in invalid)
                cached = await call("query_wind_data", {"method": "wsd", "arguments": args, "max_age_seconds": 60})
                record("budget_existing_cache_readable_with_invalid_config", cached.get("ok") and cached["from_cache"]
                       and cached["receipt_id"] == fixture["receipt_id"] and cached["raw"]["Data"] == [[1]]
                       and not cached["local_request_budget"]["native_request_reserved"])
                status = await call("wind_status", {"connect": False})
                with store.connect() as con:
                    attempts = con.execute("SELECT COUNT(*) FROM query_budget_attempts").fetchone()[0]
                record("budget_preflight_no_new_receipt_or_native_attempt", status.get("ok")
                       and status["local_request_budget"]["valid"] is False and attempts == 0
                       and len(list(store.receipts.iterdir())) == 1 and not (profile / "native.lock").exists())


async def verify_edb_match_context(entry, record):
    """Replay a labeled synthetic cache through MCP with native requests disabled."""
    from wind_bridge.storage import Store
    with tempfile.TemporaryDirectory(prefix="wind-mcp-edb-context-") as directory:
        temporary = Path(directory)
        profile = temporary / "profile"
        store = Store(profile)
        (profile / "query-budget.json").write_text(json.dumps({"max_native_calls": 0}), encoding="utf-8")
        text = "测试:库存:品种甲\n测试:库存:品种乙"
        entity = {"entity": "库存", "fullName": "测试库存", "id": "M123456", "type": "edbIndex",
                  "startIndex": 3, "endIndex": 4,
                  "candidateEntities": [{"entity": "库存", "id": code, "type": "edbIndex"}
                                        for code in ("M123456", "M123457")]}
        payload = {"status": "0", "body": {"status_code": 200, "succeed": True,
                                             "data": [[entity], [entity]]}}
        fixture = store.save("wai", {"func": "fer", "input": text, "options": ""}, {
            "ok": True, "verification_fixture": True,
            "raw": {"ErrorCode": 0, "Codes": ["fer"], "Fields": ["details"], "Times": [],
                    "Data": [[json.dumps(payload, ensure_ascii=False)]]}})
        params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=directory,
            env={**(entry.get("env") or {}), "WIND_TERMINAL_MCP_DATA_DIR": str(profile),
                 "WIND_TERMINAL_MODULE_DIR": str(temporary / "absent-wind-sdk")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=60)) as client:
                await client.initialize()
                async def call(name, arguments):
                    response = await client.call_tool(name, arguments)
                    value = json.loads(next(item.text for item in response.content if item.type == "text"))
                    if response.isError or value != response.structuredContent:
                        raise RuntimeError(f"EDB context text/structured contract failed: {name}")
                    return value
                status = await call("wind_status", {"connect": False})
                record("edb_context_fixture_native_requests_disabled", status.get("ok")
                       and not status["module_exists"] and status["local_request_budget"]["configured"])
                result = await call("recognize_wind_entities", {"text": text, "max_age_seconds": 60})
                occurrences = result.get("edb_candidates", [{}])[0].get("occurrences", [])
                record("edb_context_repeated_code_keeps_missing_qualifiers", result.get("ok")
                       and result.get("from_cache") and result["receipt_id"] == fixture["receipt_id"]
                       and len(occurrences) == 4
                       and [o["paragraph_index"] for o in occurrences] == [0, 0, 1, 1]
                       and all(o["matched_text"] == "库存" and o["text_match_scope"] == "partial_paragraph"
                               for o in occurrences)
                       and [o["unmatched_text"] for o in occurrences][::2] == [
                           {"before": "测试:", "after": ":品种甲"}, {"before": "测试:", "after": ":品种乙"}]
                       and len([w for w in result["warnings"] if w["code"] == "EDB_PARTIAL_TEXT_MATCH"]) == 2)
                search = await call("search_economic_indicator", {
                    "question": text, "scope": "terminal", "limit": 1, "max_age_seconds": 60})
                coverage = search.get("edb_text_coverage", {})
                record("edb_context_search_page_preserves_all_paragraphs", search.get("ok")
                       and search.get("receipt_id") == fixture["receipt_id"]
                       and search.get("total_matches") == 2 and search.get("next_offset") == 1
                       and len(search.get("metrics", [])) == 1
                       and search["metrics"][0]["candidate"]["occurrences"] == occurrences
                       and len(coverage.get("paragraphs", [])) == 2
                       and all(r["candidate_codes"] == ["M123456", "M123457"]
                               and r["status"] == "partial_text_candidates_only" and not r["selection_verified"]
                               for r in coverage["paragraphs"]))
                with store.connect() as con:
                    attempts = con.execute("SELECT COUNT(*) FROM query_budget_attempts").fetchone()[0]
                record("edb_context_replay_does_not_query_or_register", attempts == 0
                       and store.catalog() == [] and len(list(store.receipts.iterdir())) == 1
                       and not (profile / "native.lock").exists())


async def verify_edb_date_diagnostics(entry, record):
    """Replay mixed EDB dates through both MCP paths without a Wind connection."""
    from wind_bridge.storage import Store
    with tempfile.TemporaryDirectory(prefix="wind-mcp-edb-dates-") as directory:
        temporary = Path(directory)
        profile = temporary / "profile"
        store = Store(profile)
        (profile / "query-budget.json").write_text(json.dumps({"max_native_calls": 0}), encoding="utf-8")
        args = {"codes": "M123456,M123457,M123458", "beginTime": "2026-01-01", "endTime": "2026-09-30", "options": ""}
        fixture = store.save("edb", args, {"ok": True, "verification_fixture": True, "raw": {
            "ErrorCode": 0, "Codes": args["codes"].split(","), "Fields": ["close"],
            "Times": ["2024-12-31", "2026-01-02", "2026-09-30"],
            "Data": [[None, 0, 5], [70.1, None, None], [None, None, None]]}})
        params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=directory,
            env={**(entry.get("env") or {}), "WIND_TERMINAL_MCP_DATA_DIR": str(profile),
                 "WIND_TERMINAL_MODULE_DIR": str(temporary / "absent-wind-sdk")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=60)) as client:
                await client.initialize()
                async def call(name, arguments):
                    response = await client.call_tool(name, arguments)
                    value = json.loads(next(item.text for item in response.content if item.type == "text"))
                    if response.isError or value != response.structuredContent:
                        raise RuntimeError(f"EDB date text/structured contract failed: {name}")
                    return value
                failed = await call("query_wind_data", {"method": "edb", "arguments": args, "max_age_seconds": 60})
                observed = failed.get("edb_series_observations", {})
                record("edb_dates_failed_batch_identifies_actual_stale_column", failed.get("ok") is False
                       and failed.get("code") == "OUT_OF_RANGE_DATA" and failed.get("receipt_id") == fixture["receipt_id"]
                       and observed.get("populated_out_of_range_codes") == ["M123457"]
                       and [s["status"] for s in observed.get("series", [])] == [
                           "values_only_inside_requested_interval", "values_only_outside_requested_interval",
                           "no_non_null_values_in_returned_matrix"]
                       and observed["series"][0]["in_range"]["zero_count"] == 1)
                page = await call("read_wind_receipt", {"receipt_id": fixture["receipt_id"], "offset": 1, "limit": 1})
                saved = page.get("receipt", {})
                record("edb_dates_pagination_keeps_full_diagnostics_and_failure", page.get("ok")
                       and saved.get("edb_series_observations") == observed
                       and saved.get("stored_query_validation", {}).get("status") == "failed"
                       and saved["stored_query_validation"]["detail"]["code"] == "OUT_OF_RANGE_DATA"
                       and saved["response"]["raw"]["Times"] == ["2026-01-02"]
                       and saved["response"]["raw"]["Data"] == [[0], [None], [None]])
                with store.connect() as con:
                    attempts = con.execute("SELECT COUNT(*) FROM query_budget_attempts").fetchone()[0]
                    observations = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
                record("edb_dates_no_retry_fill_import_or_raw_change", attempts == observations == 0
                       and len(list(store.receipts.iterdir())) == 1 and store.catalog() == []
                       and store.read(fixture["receipt_id"])["sha256"] == fixture["sha256"]
                       and not (profile / "native.lock").exists())


async def verify_edb_catalog_codes(entry, record):
    """Check code selection over stdio without modifying the user's catalog."""
    from wind_bridge.storage import Store
    with tempfile.TemporaryDirectory(prefix="wind-mcp-edb-codes-") as directory:
        temporary = Path(directory)
        profile = temporary / "profile"
        store = Store(profile)
        (profile / "query-budget.json").write_text(json.dumps({"max_native_calls": 0}), encoding="utf-8")
        for row in [
            {"code": "S5708175", "name": "高炉开工率(163家)"},
            {"code": "S5100860", "name": "多晶硅产量", "source": "Wind", "freq": "年", "unit": "吨"},
            {"code": "M0325687", "name": "国债收益率:10年"},
            {"code": "H6924748", "name": "螺纹钢社会库存", "source": "Wind"},
            {"code": "K4569350", "name": "螺纹钢社会库存", "source": "根据新闻整理"},
        ]:
            store.put_indicator(row, {"metadata_source_id": "verification_fixture", "evidence": "Synthetic metadata; no Wind query"})
        original = store.catalog()
        params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=directory,
            env={**(entry.get("env") or {}), "WIND_TERMINAL_MCP_DATA_DIR": str(profile),
                 "WIND_TERMINAL_MODULE_DIR": str(temporary / "absent-wind-sdk")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=60)) as client:
                await client.initialize()
                async def search(question, **kwargs):
                    response = await client.call_tool("search_economic_indicator", {"question": question, "scope": "local", **kwargs})
                    value = json.loads(next(item.text for item in response.content if item.type == "text"))
                    if response.isError or value != response.structuredContent:
                        raise RuntimeError("EDB code search text/structured contract failed")
                    return value
                exact = await search("  s5100860  ")
                record("edb_catalog_exact_code_excludes_similar_name_digits", exact.get("ok")
                       and exact.get("match_mode") == "exact_code" and exact.get("requested_code") == "S5100860"
                       and [r["code"] for r in exact.get("metrics", [])] == ["S5100860"]
                       and exact["metrics"][0]["source"] == "Wind")
                missing = await search("S5716165")
                filtered = await search("S5100860", filters={"freq": "周"})
                record("edb_catalog_missing_or_filtered_code_has_no_fuzzy_fallback", missing.get("ok")
                       and missing.get("total_matches") == 0 and missing.get("metrics") == []
                       and filtered.get("ok") and filtered.get("total_matches") == 0)
                named = await search("螺纹钢社会库存")
                record("edb_catalog_same_name_keeps_distinct_sources_and_codes", named.get("ok")
                       and named.get("match_mode") == "lexical_name"
                       and {r["code"] for r in named.get("metrics", [])} == {"H6924748", "K4569350"}
                       and {r["source"] for r in named["metrics"]} == {"Wind", "根据新闻整理"}
                       and all(r["windpy_access"] == "not_implied_by_catalog" for r in named["metrics"]))
                with store.connect() as con:
                    attempts = con.execute("SELECT COUNT(*) FROM query_budget_attempts").fetchone()[0]
                record("edb_catalog_lookup_has_no_native_attempt_or_catalog_mutation", attempts == 0
                       and store.catalog() == original and list(store.receipts.iterdir()) == []
                       and not (profile / "native.lock").exists())


async def verify(live, config, output):
    config = config.expanduser().absolute()
    output = output.expanduser().absolute() if output else ROOT / "verification" / ("delivery-live.json" if live else "delivery-local.json")
    if output == config:
        raise ValueError("The report path must differ from the client configuration.")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "live": live,
              "client": "official Python MCP SDK", "configuration": str(config),
              "vendor_harness_execution": False, "checks": []}

    def record(name, passed, **detail):
        report["checks"].append({"name": name, "passed": bool(passed), **detail})
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"name": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)
        if not passed:
            raise RuntimeError(name)

    try:
        entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["wind_terminal_api"]
        with tempfile.TemporaryDirectory(prefix="wind-mcp-cwd-") as foreign:
            params = StdioServerParameters(command=entry["command"], args=entry["args"],
                                           env=entry.get("env"), cwd=foreign)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=60)) as client:
                    initialized = await client.initialize()
                    record("stdio_initialize", bool(initialized.serverInfo.name), protocol=initialized.protocolVersion)
                    listing = await client.list_tools()
                    names = {tool.name for tool in listing.tools}
                    record("tools_and_schemas", {"query_wind_data", "plan_wind_data_queries", "search_wind_documentation", "search_wind_query_recipes", "wind_status"} <= names
                           and all(tool.inputSchema.get("type") == "object" and (tool.outputSchema or {}).get("type") == "object"
                                   for tool in listing.tools), tool_count=len(names))

                    async def call(name, args):
                        response = await client.call_tool(name, args)
                        value = json.loads(next(item.text for item in response.content if item.type == "text"))
                        if response.isError or value != response.structuredContent:
                            raise RuntimeError(f"Text/structured contract failed: {name}")
                        return value

                    status = await call("wind_status", {"connect": False})
                    record("local_sdk", status.get("ok") and status.get("module_exists"), response=status)
                    quota = status.get("quota_observation", {})
                    record("local_quota_is_not_live_balance", quota.get("live_quota_checked") is False
                           and quota.get("current_quota_available") is None
                           and quota.get("current_reset_at") is None
                           and quota.get("documented_general_policy", {}).get("window_hours") == 168
                           and quota["documented_general_policy"]["function_limits"] is None
                           and not quota["documented_general_policy"]["recovery_time_computable"]
                           and quota.get("search_scope") == "recent_failed_receipts_in_this_local_profile_only")
                    capability = await call("wind_capabilities", {})
                    platform_policy = capability.get("platform_policy", {})
                    record("desktop_platform_policy_and_runtime_are_discoverable",
                           status.get("platform_support", {}).get("supported") is True
                           and capability.get("platform_support", {}).get("profile") == status["platform_support"]["profile"]
                           and platform_policy.get("supported_desktops") == ["macOS", "Windows", "中科方德 5.0", "UOS 20", "银河麒麟 V10 SP1"]
                           and "all Linux server editions" in platform_policy.get("unsupported", [])
                           and platform_policy.get("policy_match_is_not_native_validation") is True
                           and platform_policy.get("windows_and_xinchuang_native_validation") == "not_run_on_target_hardware")
                    contracts = capability.get("request_contract_notes", {})
                    record("manual_contract_constraints_are_discoverable", contracts.get("wses", {}).get("fields_per_request") == 1
                           and contracts.get("wsee", {}).get("existing_multi_field_route_preserved") is True
                           and contracts.get("wsee", {}).get("multi_field_runtime_verified") is False)
                    record("budget_metadata_distinct_from_account_quota", capability.get("local_request_budget", {}).get("shared_atomic_reservations") is True
                           and capability["local_request_budget"]["account_quota"] is False
                           and status.get("local_request_budget", {}).get("valid") is True)
                    invalid_sector = await call("query_wind_data", {"method": "wses", "arguments": {
                        "codes": "a001010100", "fields": "sec_close_avg,sec_pe_avg",
                        "beginTime": "2026-09-28", "endTime": "2026-09-29"}})
                    record("multi_field_wses_rejected_before_wind", invalid_sector.get("code") == "INVALID_PARAMS"
                           and "receipt_id" not in invalid_sector)
                    invalid_time = await call("query_wind_data", {"method": "wsi", "arguments": {
                        "codes": "AL00.SHF", "fields": "close", "beginTime": "2026-09-29 15:00:00",
                        "endTime": "2026-09-29 09:00:00"}})
                    record("reversed_intraday_time_rejected_before_wind", invalid_time.get("code") == "INVALID_PARAMS"
                           and "receipt_id" not in invalid_time)
                    plan_args = {"method": "wsd", "arguments": {"codes": [f"TEST{i}.SHF" for i in range(90)],
                                 "fields": "open,close,oi", "beginTime": "2026-09-21", "endTime": "2026-09-29",
                                 "options": "Period=D;Fill=Blank"}, "limit": 2}
                    planned = await call("plan_wind_data_queries", plan_args)
                    record("offline_batch_plan_keeps_full_input", planned.get("ok")
                           and planned["total_requests"] == 6 and planned["next_offset"] == 2
                           and planned["input_shape"]["code_field_pairs"] == 270
                           and planned["native_calls"] == 0 and not planned["executed"]
                           and all(r["arguments"]["options"] == "Period=D;Fill=Blank" for r in planned["requests"])
                           and not planned["quota_observation"]["live_quota_checked"])
                    record("plan_volume_estimates_full_input_and_page", planned["data_volume_estimate"]["estimated_cells"] == 90 * 3 * 9
                           and [r["data_volume_estimate"]["estimated_cells"] for r in planned["requests"]] == [50 * 9, 40 * 9]
                           and not planned["data_volume_estimate"]["billing_estimate"])
                    continuation = await call("plan_wind_data_queries", {**plan_args, "offset": 2})
                    record("offline_batch_plan_pages_are_stable", continuation.get("ok")
                           and continuation["plan_id"] == planned["plan_id"]
                           and continuation["requests"][0]["index"] == 2 and not continuation["executed"])
                    help_result = await call("search_wind_documentation", {"question": "wss", "document": "python", "limit": 2})
                    if help_result.get("code") == "LOCAL_REFERENCE_NOT_INSTALLED":
                        record("optional_documentation_missing_is_explicit", help_result.get("ok") is False
                               and help_result.get("reference_available") is False and not help_result["executed"])
                        bundle = await call("search_wind_bundle_metadata", {"question": "close"})
                        record("optional_bundle_reference_status", bundle.get("ok") or (
                               bundle.get("code") == "LOCAL_REFERENCE_NOT_INSTALLED"
                               and bundle.get("reference_available") is False and not bundle["executed"]))
                    else:
                        record("locally_imported_documentation", help_result.get("ok") and help_result.get("results"))
                        index = json.loads((ROOT / "references/official-help/index.json").read_text(encoding="utf-8"))
                        if any(d["document"] == "futures_rules" for d in index["documents"]):
                            rules = await call("search_wind_documentation", {"question": "近月", "document": "futures_rules", "limit": 10})
                            record("locally_imported_futures_rule_summary", rules.get("ok") and rules.get("results")
                                   and any(20 in r.get("pdf_pages", []) and "CU00.SHF" in r["text"] for r in rules["results"])
                                   and all(r.get("metadata_source_id") == "wind_terminal_ui"
                                           and r.get("content_kind") == "paraphrased_terminal_pdf_observation"
                                           and r.get("original_pdf_saved") is False
                                           and r.get("local_raw_copy_matches_import") is True for r in rules["results"])
                                   and not rules["executed"] and not rules["remote_freshness_checked"])
                    field = await call("search_wind_fields", {"question": "close", "method": "wsd"})
                    record("packaged_field_discovery", field.get("ok") and field.get("fields"))
                    candidates = await call("search_wind_fields", {"question": "开始交易日", "method": "wss", "scope": "candidates"})
                    record("packaged_field_candidates", candidates.get("ok") and candidates["catalog_field_count"] == 6865
                           and any(r["field"] == "ftdate" for r in candidates["fields"])
                           and all(r["candidate_only"] and not r["runtime_query_verified"] and r["options_schema"] is None for r in candidates["fields"])
                           and candidates["catalog_fingerprint_matches"] and not candidates["executed"])
                    recipes = await call("search_wind_query_recipes", {"question": "可交割券", "method": "wset"})
                    record("packaged_query_recipes", recipes.get("ok") and recipes.get("recipes")
                           and any(r["arguments"].get("tablename") == "conversionfactor" for r in recipes["recipes"])
                           and not recipes["executed"] and not recipes["current_entitlement_checked"])
                    edb_dates = await call("search_wind_query_recipes", {"question": "碳酸锂 日期越界", "method": "edb"})
                    record("packaged_edb_date_counterexample_remains_failed", edb_dates.get("ok")
                           and len(edb_dates.get("recipes", [])) == 1
                           and edb_dates["recipes"][0].get("historical_query_validation_status") == "failed"
                           and edb_dates["recipes"][0].get("historical_query_error") == "OUT_OF_RANGE_DATA"
                           and not edb_dates["recipes"][0]["runtime_query_verified"]
                           and edb_dates["recipes"][0]["evidence_issue"] == "NO_RECEIPT"
                           and edb_dates["recipes"][0]["historical_edb_series_observations"]["populated_out_of_range_codes"] == ["S5449460"]
                           and not edb_dates["executed"])
                    mapping = await call("search_wind_query_recipes", {"question": "主连 映射", "method": "wsd"})
                    record("packaged_virtual_mapping_recipe", mapping.get("ok")
                           and any(r["name"] == "futures_virtual_contract_historical_mapping"
                                   and r["arguments"].get("codes") == "CU.SHF,CU_S.SHF,CU00.SHF,T.CFE,T_S.CFE,T00.CFE"
                                   and r["arguments"].get("fields") == "trade_hiscode"
                                   and r.get("price_adjustment_certified") is False
                                   and r.get("point_in_time_safe") is False
                                   and r.get("parameter_semantics_certified") is False
                                   for r in mapping.get("recipes", []))
                           and not mapping["executed"] and not mapping["current_entitlement_checked"])
                    warehouse = await call("search_wind_query_recipes", {"question": "仓单 六日", "method": "wsd"})
                    warehouse_codes = [r["arguments"]["codes"].split(",") for r in warehouse.get("recipes", [])]
                    record("packaged_warehouse_bounded_history", warehouse.get("ok")
                           and len(warehouse_codes) == 2 and sorted(map(len, warehouse_codes)) == [31, 50]
                           and len({code for codes in warehouse_codes for code in codes}) == 81
                           and all(r["arguments"].get("fields") == "st_stock"
                                   and r.get("point_in_time_safe") is False
                                   and r.get("parameter_semantics_certified") is False
                                   for r in warehouse["recipes"])
                           and not warehouse["executed"] and not warehouse["current_entitlement_checked"])
                    named = await call("search_wind_query_recipes", {"question": "沪铝 仓单 六日", "method": "wsd"})
                    monthly = await call("search_wind_query_recipes", {"question": "塑料月均价 仓单 六日", "method": "wsd"})
                    named_products = [p for r in named.get("recipes", [])
                                      for p in r.get("product_discovery", {}).get("products", [])
                                      if p["product_id"] == "AL:standard:SHF"]
                    monthly_products = [p for r in monthly.get("recipes", [])
                                        for p in r.get("product_discovery", {}).get("products", [])
                                        if p["product_id"] == "L:F:DCE"]
                    record("packaged_named_futures_recipes_keep_nulls_and_evidence_scope",
                           named.get("ok") and monthly.get("ok")
                           and len(named_products) == len(monthly_products) == 1
                           and named_products[0]["query_code"] == "AL00.SHF"
                           and named_products[0]["historical_value_counts"]["st_stock"]["populated_count"] == 6
                           and monthly_products[0]["query_code"] == "L_F00.DCE"
                           and monthly_products[0]["historical_value_counts"]["st_stock"]["missing_count"] == 6
                           and all(not r["runtime_query_verified"] and r.get("evidence_issue") == "NO_RECEIPT"
                                   and not r["product_discovery"]["query_arguments_modified"]
                                   and not r["product_discovery"]["automatic_product_selection"]
                                   for r in [*named["recipes"], *monthly["recipes"]])
                           and not named["executed"] and not monthly["executed"])
                    ctd = await call("search_wind_query_recipes", {"question": "ctd", "method": "wset"})
                    record("packaged_ctd_recipe", ctd.get("ok") and any(r["arguments"].get("tablename") == "ctd" for r in ctd["recipes"])
                           and not ctd["executed"])
                    ctd_contracts = {r["arguments"]["options"].split("windcode=")[-1] for r in ctd["recipes"]
                                     if r["arguments"].get("tablename") == "ctd"}
                    record("packaged_four_treasury_ctd_products", {"TS2612.CFE", "TF2612.CFE", "T2612.CFE", "TL2612.CFE"} <= ctd_contracts)
                    valuation = await call("search_wind_query_recipes", {"question": "沪深300 长历史", "method": "wsd"})
                    record("packaged_index_valuation_history", valuation.get("ok")
                           and any(r["arguments"].get("codes") == "000300.SH" and "pe_ttm" in r["arguments"].get("fields", "") for r in valuation["recipes"])
                           and not valuation["executed"])
                    rates = await call("search_wind_query_recipes", {"question": "国债曲线候选", "method": "edb"})
                    record("packaged_yield_curve_zero_warning", rates.get("ok") and len(rates["recipes"]) == 1
                           and len(rates["recipes"][0]["unresolved_zero_only_codes"]) == 4
                           and rates["recipes"][0]["usable_government_yield_curves_verified"] is False)
                    csi1000 = await call("search_wind_query_recipes", {"question": "中证1000 长历史", "method": "wsd"})
                    record("packaged_recovered_csi1000_history", csi1000.get("ok")
                           and any(r["arguments"].get("codes") == "000852.SH" and r["receipt_id"] == "3cb60468958d444392a459748693c5b0" for r in csi1000["recipes"])
                           and not csi1000["executed"])
                    chinamoney = await call("search_wind_query_recipes", {"question": "中国货币网 10年", "method": "edb"})
                    record("packaged_chinamoney_history_distinct_publisher", chinamoney.get("ok")
                           and any(r["arguments"].get("codes") == "M0325687"
                                   and r["reviewed_indicator_metadata"]["source"] == "中国货币网"
                                   and r["reviewed_indicator_metadata"]["unit"] == "%"
                                   and r["different_publisher_not_substitution_for_chinabond"]
                                   for r in chinamoney["recipes"])
                           and not chinamoney["executed"])
                    soda = await call("search_wind_query_recipes", {"question": "纯碱 隆众资讯 历史", "method": "edb"})
                    record("packaged_soda_history_keeps_irregular_frequency", soda.get("ok")
                           and any(r["arguments"].get("codes") == "S5479786"
                                   and r["arguments"]["beginTime"] == "2018-04-04"
                                   and r["history_observation"]["non_null_observations"] == 538
                                   and r["history_observation"]["counts_by_year"]["2025"] == 97
                                   and r["reviewed_indicator_metadata"]["unit"] == "万吨"
                                   and r["reviewed_indicator_metadata"]["source"] == "隆众资讯"
                                   and not r["history_observation"]["regular_weekly_calendar_certified"]
                                   and not r["point_in_time_safe"] for r in soda["recipes"])
                           and not soda["executed"])
                    scfi = await call("search_wind_query_recipes", {"question": "SCFI 综合指数 历史", "method": "edb"})
                    record("packaged_scfi_history_preserves_base_and_benchmark_scope", scfi.get("ok")
                           and any(r["arguments"].get("codes") == "S0114089"
                                   and r["arguments"]["beginTime"] == "2009-10-16"
                                   and r["history_observation"]["non_null_observations"] == 850
                                   and r["reviewed_indicator_metadata"]["unit"] == "2009年10月16日=1000"
                                   and "不能当作EC期货交割标的" in r["note"]
                                   and not r["calendar_completeness_certified"]
                                   and not r["point_in_time_safe"] for r in scfi["recipes"])
                           and not scfi["executed"])
                    positions = await call("search_wind_query_recipes", {"question": "前20名 净持仓 长历史", "method": "wsd"})
                    rebar_codes = await call("search_wind_query_recipes", {"question": "螺纹钢 H代码 K代码", "method": "edb"})
                    record("packaged_rebar_terminal_codes_keep_separate_sources", rebar_codes.get("ok")
                           and any(r["arguments"].get("codes") == "S5716165,H6924748,K4569350"
                                   and [x["non_null_count"] for x in r["historical_per_code_observations"]] == [1, 2, 1]
                                   and [x["metadata"]["source"] for x in r["historical_per_code_observations"]] == [None, "Wind", "根据新闻整理"]
                                   and not r["aliases_certified"] for r in rebar_codes["recipes"])
                           and not rebar_codes["executed"])
                    annual_production = await call("search_wind_query_recipes", {"question": "碳酸锂 多晶硅 年度产量", "method": "edb"})
                    record("packaged_annual_production_preserves_nulls_and_different_units", annual_production.get("ok")
                           and any(r["arguments"].get("codes") == "S5449460,S5100860"
                                   and r["arguments"]["endTime"] == "2024-12-31"
                                   and [x["non_null_count"] for x in r["historical_per_code_observations"]] == [16, 24]
                                   and [x["metadata"]["unit"] for x in r["historical_per_code_observations"]] == ["万吨", "吨"]
                                   and r["null_cells"] == 8 and r["non_null_values"] == 40
                                   and not r["point_in_time_safe"] for r in annual_production["recipes"])
                           and not annual_production["executed"])
                    record("packaged_member_history_parameter_evidence", positions.get("ok")
                           and any(r["arguments"].get("fields") == "oi_nvoi"
                                   and r["arguments"].get("options") == "order=40"
                                   and r["parameter_evidence"]["lookup_label"] == "前20名合计"
                                   and not r["parameter_evidence"]["ranking_methodology_certified"]
                                   and not r["point_in_time_safe"] for r in positions["recipes"])
                           and not positions["executed"])
                    rank1 = await call("search_wind_query_recipes", {"question": "member_product_rank1_20261003"})
                    record("packaged_rank_one_counterexample", rank1.get("ok") and len(rank1["recipes"]) == 1
                           and rank1["recipes"][0]["arguments"]["options"] == "tradeDate=20260929;order=1"
                           and "不得按榜次混算" in rank1["recipes"][0]["note"] and not rank1["executed"])
                    net = await call("search_wind_fields", {"question": "净持仓(品种)", "method": "wsd"})
                    record("packaged_native_net_position_field", net.get("ok")
                           and any(f["field"] == "oi_nvoi" and f["unit"] is None for f in net["fields"])
                           and not net["executed"])
                    financial_positions = await call("search_wind_query_recipes", {"question": "八类金融期货 净持仓 长历史", "method": "wsd"})
                    record("packaged_eight_financial_position_histories", financial_positions.get("ok")
                           and any(len(r["arguments"]["codes"].split(",")) == 8
                                   and sum(v["non_null_rows"] for v in r["historical_sample_statistics"].values()) == 19305
                                   and "quota exceeded" in r["note"] and not r["point_in_time_safe"]
                                   for r in financial_positions["recipes"])
                           and not financial_positions["executed"])
                    bounded_positions = await call("search_wind_query_recipes", {"question": "净持仓 双日", "method": "wsd"})
                    record("packaged_bounded_member_requests_keep_dates_and_nulls", bounded_positions.get("ok")
                           and len(bounded_positions["recipes"]) == 2
                           and sum(len(r["arguments"]["codes"].split(",")) for r in bounded_positions["recipes"]) == 78
                           and sum(r["sample_statistics"]["returned_cells"] for r in bounded_positions["recipes"]) == 156
                           and sum(r["sample_statistics"]["null_cells"] for r in bounded_positions["recipes"]) == 33
                           and all(r["arguments"]["beginTime"] == "2026-09-28"
                                   and r["arguments"]["endTime"] == "2026-09-29"
                                   and r["arguments"]["fields"] == "oi_nvoi"
                                   and r["arguments"]["options"] == "order=40"
                                   and not r["full_history_verified"] for r in bounded_positions["recipes"])
                           and not bounded_positions["executed"])
                    member_routes = capability["data_coverage"]["futures_acceptance"].get("member_two_day_routes", {})
                    record("packaged_daily_route_coverage_does_not_inflate_long_history", member_routes.get("products") == 90
                           and member_routes["complete_products"] == 73 and member_routes["partial_products"] == 1
                           and member_routes["all_null_products"] == 16
                           and member_routes["numeric_endpoint_matches"] == 73 and member_routes["null_endpoint_matches"] == 17
                           and member_routes["completed_long_history_samples_unchanged"] == 12
                           and not member_routes["full_history_verified"] and not member_routes["current_product_census_verified"])
                    invalid = await call("query_wind_data", {"method": "wupf", "arguments": {}})
                    record("structured_error", invalid.get("code") == "METHOD_NOT_ALLOWED")
                    exported = await call("parse_wind_edb_export", {"csv_text": "指标名称,示例\n指标ID,S0026989\n频率,月\n单位,万吨\n来源,国家统计局\n",
                                                                  "evidence": "Installation verification fixture, not an actual export"})
                    record("packaged_edb_export_parser", exported.get("ok") and exported["indicator_count"] == 1
                           and exported["indicators"][0]["export_metadata"]["unit"] == "万吨"
                           and exported["indicators"][0]["unit"] is None and not exported["native_metadata_promoted"]
                           and not exported["imported"] and not exported["executed"])
                    if live:
                        args = {"method": "wss", "arguments": {"codes": "600519.SH", "fields": "sec_name,close", "options": "tradeDate=20260928"}}
                        value = await call("query_wind_data", args)
                        record("live_wss", value.get("ok") and value.get("data_source_id") == "wind_terminal_api"
                               and value.get("raw", {}).get("Codes") == ["600519.SH"]
                               and value["raw"]["Data"][0] == ["贵州茅台"] and value["raw"]["Data"][1][0] is not None,
                               args=args, response=value)
                        receipt = await call("read_wind_receipt", {"receipt_id": value["receipt_id"]})
                        record("receipt_roundtrip", receipt.get("ok") and receipt["receipt"]["response"]["raw"] == value["raw"])
                    report["status"] = "passed"
        await verify_budget_preflight(entry, record)
        await verify_edb_match_context(entry, record)
        await verify_edb_date_diagnostics(entry, record)
        await verify_edb_catalog_codes(entry, record)
    except Exception as exc:
        report["status"], report["error"] = "failed", str(exc)
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report["status"] == "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, default=ROOT / "client-configs/mcp.json",
                        help="Generated MCP JSON configuration to launch")
    parser.add_argument("--output", type=Path, help="Separate JSON verification report path")
    options = parser.parse_args()
    sys.exit(0 if asyncio.run(verify(options.live, options.config, options.output)) else 1)
