"""Wind 终端 API MCP. Run this file with the project's isolated Python."""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings
from mcp.types import ToolAnnotations

from wind_bridge import VERSION
from wind_bridge.analytics import aggregate_snapshot, combine_edb, screen_snapshot, series_analysis
from wind_bridge.bundle_metadata import search_bundle
from wind_bridge.codegen import parse_query
from wind_bridge.common import ROOT, Problem, dumps, envelope, failure
from wind_bridge.compat import capabilities, execute
from wind_bridge.documentation import search_help
from wind_bridge.entities import recognize
from wind_bridge.edb_export import parse_export
from wind_bridge.fields import FieldCatalog
from wind_bridge.fundamental_questions import plan_fundamentals
from wind_bridge.requests import METHODS
from wind_bridge.query_recipes import QueryRecipes
from wind_bridge.query_plan import plan_queries
from wind_bridge.field_candidates import FieldCandidates
from wind_bridge.risk import risk_analysis
from wind_bridge.service import Service
from wind_bridge.platform_support import inspect_platform, support_policy
from wind_bridge.universe import screen_universe

service = Service()
# The pinned SDK's Settings references FastMCP before that class is defined.
Settings.model_rebuild()
mcp = FastMCP("Wind 终端 API MCP", instructions="General multi-asset Wind terminal service: equities, bonds, funds, indices, futures, options, FX, macro and industry data. No agricultural priority or field whitelist. Independent wind_terminal_api, not Alice MCP; only local WindPy reads. For EDB discovery outside saved local metadata, use search_economic_indicator(scope='terminal'); its WAI candidates are not exhaustive or automatically selected. Inspect capability gaps, provenance, dates, units and publication-time limitations. Never infer terminal entitlement from imported metadata.")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
LOCAL_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Problem as exc:
        return failure(exc)
    except Exception as exc:
        logging.exception("Wind MCP local failure")
        return failure(exc)


@mcp.tool(annotations=READ)
def wind_status(connect: bool = False) -> dict[str, Any]:
    """Inspect local WindPy, local request budget, historical quota errors and any later identical successful request. No live quota balance is available. connect=true tests login (counts as a budget attempt), not all dataset permissions. The configurable local budget is shared only by updated servers using the same data directory; cache and local reads spend no attempts. Never contacts Alice."""
    return run(service.backend.status, connect)


@mcp.tool(annotations=LOCAL_READ)
def wind_capabilities() -> dict[str, Any]:
    """List native WindPy read signatures and local SDK limitations. Also includes optional Alice compatibility mappings; full_parity=false does not prevent generic Wind queries."""
    return {**capabilities(), "version": VERSION,
            "platform_support": inspect_platform(), "platform_policy": support_policy(),
            "data_coverage": json.loads((ROOT / "references/data-coverage.json").read_text(encoding="utf-8")),
            "local_query_planning": {"tool": "plan_wind_data_queries", "methods": ["wsd", "wss"],
                                     "native_calls": 0, "options_and_dates_preserved": True,
                                     "fewer_requests_does_not_reduce_data_volume": True,
                                     "data_volume_estimate_included": True},
            "local_request_budget": {"policy_file": "query-budget.json in WIND_TERMINAL_MCP_DATA_DIR",
                                     "shared_atomic_reservations": True, "default_limits": None,
                                     "limits": ["max_native_calls", "max_estimated_cells", "max_estimated_cells_per_request"],
                                     "account_quota": False,
                                     "documentation": "references/query-budget.md"},
            "request_contract_notes": {
                "wses": {"fields_per_request": 1, "multiple_sector_codes_allowed": True,
                         "evidence": "2025-04-10 API manual section 2.1.6; saved Python and Client API help"},
                "wsee": {"single_field_documentation_conflicts_with_client_api_multi_field_description": True,
                         "existing_multi_field_route_preserved": True, "multi_field_runtime_verified": False},
                "wsi_wst": {"single_code_is_local_constraint": True,
                             "local_iso_timestamps_required": True, "full_time_order_validated": True},
                "relative_date_resolution": "not_implemented; use explicit dates because D/TD examples conflict with the manual's dedicated macro section"
            },
            "native_read_methods": {
        method: {"required_arguments": req, "optional_arguments": opt}
        for method, (req, opt) in METHODS.items()
    }}


@mcp.tool(annotations=READ)
def search_economic_indicator(question: str, limit: int = 20, offset: int = 0,
                              filters: dict | None = None, scope: str = "local",
                              max_age_seconds: int = 0) -> dict[str, Any]:
    """Discover EDB candidates across industries. scope='terminal' calls official Wind WAI fer, independent of the local catalog; it returns all raw alternatives and unknown unit/frequency/source values without selecting, registering or fetching any indicator. Check edb_text_coverage and candidate.occurrences: partial matches leave qualifiers unresolved, repeated codes retain every paragraph, and full-text matches do not certify meaning. This is entity recognition, not exhaustive full-catalog search; zero matches does not mean Wind lacks data. scope='local' searches only previously registered metadata, with exact unit/freq/source filters; fresh installations start empty. Filters require local scope. Review definition, geography, units and history before choosing an EDB code; explicit code queries are never restricted to this catalog."""
    return run(service.discover_economic, question, limit, offset, filters, scope, max_age_seconds)


@mcp.tool(annotations=LOCAL_READ)
def parse_wind_edb_export(csv_text: str, evidence: str) -> dict[str, Any]:
    """Inspect decoded Wind EDB Simplified Chinese CSV in column layout, at most 4 MB and 500 indicators. Retain code/name and original display headers including unit, frequency, publisher and time range. Display metadata is not automatically promoted to native EDB units or frequency: transformations are not recorded in CSV headers. No file access, formula execution, value/catalog import or Wind call. Review original definitions and compare untransformed native values before using register_economic_indicators. UTF-8/GB18030 file decoding is also available in the bundled inspect_edb_export.py CLI."""
    return run(parse_export, csv_text, evidence)


@mcp.tool(annotations=LOCAL_READ)
def inspect_economic_catalog() -> dict[str, Any]:
    """Show local indicator counts grouped by frequency, unit, original source and metadata provider. This is not global Wind coverage or proof of dataset entitlement."""
    return run(service.catalog.summary)


@mcp.tool(annotations=READ)
def recognize_wind_entities(text: str, max_age_seconds: int = 0) -> dict[str, Any]:
    """Use verified WindPy WAI fer to recognize financial entities in 1..5000 characters. Returns EDB candidates, same-name conflicts and unchanged raw alternatives. edb_text_coverage and each candidate's occurrences retain repeated-code paragraph context, matched text and unresolved qualifiers. Missing/invalid offsets or changed paragraph counts stay unverified; full-text coverage does not certify indicator meaning. Financial alternatives get local category/label context only when node ID AND name match a current fingerprinted bundle. This is not full-catalog search or a unit/frequency/field dictionary. Numeric stockBondIndex IDs are NOT WSS fields. Does not automatically choose, register, or fetch any recognized code."""
    return run(recognize, service, text, max_age_seconds)


@mcp.tool(annotations=LOCAL_READ)
def search_wind_fields(question: str, method: str | None = None, limit: int = 20, offset: int = 0,
                       scope: str = "observed") -> dict[str, Any]:
    """Search WindPy fields by Chinese label or explicit field. scope='observed' (default) uses local UI definitions, saved query examples and reviewed issuer-statement samples. successful_query_available requires a validated saved request; successful_sample_available also requires a nonblank value. scope='candidates' searches a much larger, versioned third-party windget 0.0.7 mapping catalog (2022): these are unverified field candidates, not official definitions, options schemas or current entitlement evidence. Neither scope is the full Wind dictionary or executes queries; no automatic cross-scope fallback, field substitution or WAI-ID translation. Examples are not defaults; sample financial evidence does not certify other issuers, dates, options or units."""
    if scope == "candidates":
        return run(FieldCandidates().search, question, method, limit, offset)
    if scope != "observed":
        return failure(Problem("INVALID_PARAMS", "scope must be observed or candidates"))
    return run(FieldCatalog(service.store).search, question, method, limit, offset)


@mcp.tool(annotations=LOCAL_READ)
def search_wind_query_recipes(question: str, method: str | None = None, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    """Find locally saved explicit Wind requests by Chinese labels, table names, fields or option names. Includes futures warehouses, member ranks, continuous mappings, contract calendars and deliverable bonds. Returns original method/arguments, evidence and limitations; never executes examples or assumes their dates/options are defaults. Checks local saved receipts when available; fresh installations retain examples but do not claim those receipts or current account entitlements. This is not a complete Wind parameter dictionary."""
    return run(QueryRecipes(service.store).search, question, method, limit, offset)


@mcp.tool(annotations=LOCAL_READ)
def search_wind_bundle_metadata(question: str, kind: str = "indicator", limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Search clear Wind API.app metadata: kind=indicator for bilingual indicator labels, parameter for lookup options, or sector for system-sector IDs, category paths and related index codes. Sector names do not prove current members, market coverage or historical availability; verify a WSET query separately. This is NOT a native field dictionary: indicator expressions, node IDs and lookup values are not automatically executable fields/options. Returns all contributing file fingerprints; no Wind request or automatic selection."""
    return run(search_bundle, question, kind, limit, offset)


@mcp.tool(annotations=LOCAL_READ)
def search_wind_documentation(question: str, document: str | None = None, limit: int = 5, offset: int = 0) -> dict[str, Any]:
    """Search saved official Wind manuals/examples and clearly marked terminal PDF summaries. Keep headings, local text lines, source entry URLs and PDF page references when available. Optional document selector is listed in the response (e.g. python, api_faq, python_basic, python_sector, futures_rules). A terminal summary is not the original PDF or proof of current rules. Paginated sections keep all saved text. This is offline documentation discovery, NOT field or account validation. Never executes example code, contacts documentation endpoints or uses credentials."""
    return run(search_help, question, document, limit, offset)


@mcp.tool(annotations=LOCAL_READ)
def describe_economic_indicators(codes: list[str]) -> dict[str, Any]:
    """Return saved metadata and its original provenance. Unknown entries remain unknown, not fabricated."""
    return envelope(metrics=[service.store.indicator(code) or {"code": code, "metadata_found": False} for code in codes])


@mcp.tool(annotations=READ)
def query_economic_indicator_data(question: str, beginDate: str | None = None, endDate: str | None = None,
                                  observation: str | None = None, max_age_seconds: int = 0) -> dict[str, Any]:
    """Query confirmed EDB code(s) or an exact unambiguous catalog name. Provide YYYY-MM-DD beginDate+endDate OR observation='10'. Validate returned dates and preserve raw receipts. Values are not certified point-in-time data."""
    return run(service.economic, question, beginDate, endDate, observation, max_age_seconds)


@mcp.tool(annotations=READ)
def query_wind_data(method: str, arguments: dict, max_age_seconds: int = 0) -> dict[str, Any]:
    """Call an allowlisted official WindPy read method with documented SDK argument names. Use wind_capabilities for signatures. Fields are not restricted to the small local field catalog or optional Chinese question mappings. WSS may include statement_reference_checks for narrowly reviewed issuer-filing cells; differences are annotated without replacing raw values, and uncovered parameters are not certified. EDB responses and validation errors include per-code edb_series_observations for the full shared date axis: inspect populated in/out-of-range counts, nulls and zeros before treating a date as an observation for every indicator. An out-of-range batch remains failed; diagnostics do not trim, fill, retry or certify definitions. No usedf, subscriptions, trades, eval, or Alice fallback. max_age_seconds=0 fetches live. If error_category='quota_exceeded', preserve completed receipts and stop automatic retries/new bulk reads; remaining quota, limit scope and reset time may be unknown. Inspect wind_status(connect=false) for the last local quota observation."""
    return run(service.query, method, arguments, max_age_seconds)


@mcp.tool(annotations=LOCAL_READ)
def plan_wind_data_queries(method: str, arguments: dict, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Plan WSD/WSS batches without calling Wind. Supply explicit native arguments, including confirmed codes/fields, dates and options; accept up to 10000 codes and 500 fields. Compare code/field loops and mixed tails under this MCP's existing 50-code/100-field request limits. Return paginated executable requests, stable plan_id and zero-based stop-exclusive axis slices; fetch every page. data_volume_estimate covers the full request and each emitted batch; calendar-day estimates include nontrading days and are not Wind billing units. Preserve the full date range and exact options, with no cross-method substitution or automatic financial calendar, rptType, Fill, showblank or zero filling. Fewer network calls do not reduce requested data cells or bypass quota. Local quota observation is historical, not a live balance. No execution, retry, current-entitlement or full parameter-semantic certification."""
    return run(plan_queries, service.store, method, arguments, limit, offset)


@mcp.tool(annotations=LOCAL_READ)
def parse_windpy_query(code: str) -> dict[str, Any]:
    """Convert one literal w.method(...) line from Wind's code generator into method/arguments, without executing Python. No imports, callbacks, variables or trade methods. Parsing does not certify field semantics or data permissions."""
    return run(parse_query, code)


@mcp.tool(annotations=READ)
def plan_wind_fundamentals(question: str) -> dict[str, Any]:
    """Plan a bounded mainland-stock financial statement question using one WAI fer recognition and current verified WSS sample evidence, without fetching financial values. Supports explicit report dates or annual-report years, yuan unit, and nine sample-backed financial statement fields listed by wind_capabilities. Statement scope is explicit, or inferred with a recorded explanation when required by attributable-profit meaning; conflicting parent-only scope is rejected. Total equity includes minority interests, and signed financial expenses are preserved. Checks ALL stock candidates; an explicit code or A-share qualifier can disambiguate. WAI financial IDs never become field names. Every meaningful text span must be accounted for; interleaved per-company dates/fields, other fields, as-of/publication queries, growth/TTM, other units and overseas reports require a separate verified plan. Saves a ready or unresolved plan with recognition/source hashes. get_stock_fundamentals can execute the same bounded questions automatically; this tool is for inspecting the proposed queries."""
    return run(plan_fundamentals, service, question)


@mcp.tool(annotations=READ)
def call_alice_tool_on_windpy(server_type: str, tool_name: str, params: dict, resolved_request: dict | None = None) -> dict[str, Any]:
    """Migrate an Alice contract to local WindPy. EDB and mapped market fields/periods translate directly. Stock/fund/index price, Kline and quote windcode accepts names or explicit codes: WAI fer must match the entire name and yield one unique code across all candidates of the requested domain. Ambiguity stops the whole batch before market queries; never ranks candidates or invents suffixes. security_resolution retains native and derived evidence; explicit codes skip recognition. Daily Kline issusp='0' filters verified trade_status values BEFORE count; unknown/null statuses fail explicitly. Filtered or count-limited series retain native receipt_id and selected-view derived_receipt_id. get_stock_fundamentals also accepts bounded mainland-stock statement questions without resolved_request: explicit report dates or annual-report years, yuan unit, and the nine sample-backed fields in wind_capabilities. Attributable net profit requires consolidated scope; omitted scope is inferred with an explanation and conflicting parent-only scope is rejected. It uses typed WAI securities plus independently checked field/option mappings, retains missing values, and saves the complete plan and raw evidence. Ambiguous securities, unknown terms, interleaved dimensions and unsupported accounting semantics stop before value queries; plan_wind_fundamentals previews this flow. Other question tools require resolved_request={method,arguments,evidence}; optional analysis={kind:'series',periods_per_year,field?,window?,annual_risk_free_rate?}, {kind:'screen',conditions,sort_by?,descending?,limit?}, or {kind:'aggregate',field,operation,weight_field?}. Risk analysis uses {kind:'risk',periods_per_year,field?,annual_risk_free_rate?,confidence?,alignment?,benchmark_receipt_id?,benchmark_field?,benchmark_request?}; benchmark_request={method,arguments,evidence} queries one benchmark price series after the asset, mutually exclusive with a saved benchmark_receipt_id. Risk requires documented price inputs and matches both return interval endpoints. For search_stocks/search_funds, add universe_receipt_id from WSET/WEQS, method=wss, arguments={fields,options}, and screen analysis; continue pending results with screen_wind_universe. No complete Alice semantic parity is claimed."""
    return run(execute, service, server_type, tool_name, params, resolved_request)


@mcp.tool(annotations=LOCAL_READ)
def read_wind_receipt(receipt_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Read a saved native or derived Wind-only receipt. Native Data and corresponding time/security axes are paginated; table query metadata axes stay intact and total dimensions are preserved. EDB per-code in/out-of-range value counts and stored query validation, table-date observations and scoped WSS statement-reference comparisons inspect the entire raw snapshot before pagination. EDB shared dates are not populated observations for every code; diagnostics neither filter data nor approve a failed query. Derived receipts are returned intact, including a series selection's source ID, rule and selected view. Never reads arbitrary filesystem paths."""
    def read():
        return envelope(receipt=service.store.page(receipt_id, offset, limit))
    return run(read)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def register_economic_indicators(indicators: list[dict], metadata_source_id: str, evidence: str) -> dict[str, Any]:
    """Add confirmed indicator metadata to this local catalog. Supply code/name/unit/freq/source plus provenance. Does not grant permissions or certify publication times; does not query or modify Wind/Alice."""
    return run(service.register, indicators, metadata_source_id, evidence)


@mcp.tool(annotations=LOCAL_READ)
def compare_saved_edb_versions(code: str, beginDate: str, endDate: str) -> dict[str, Any]:
    """Compare locally saved WindPy retrieval vintages. Only detects revisions observed since this MCP began saving data; not original historical release vintages."""
    def compare():
        from wind_bridge.common import date_range
        date_range(beginDate, endDate)
        return envelope(revisions=service.store.revisions(code, beginDate, endDate),
                        scope="local_retrieval_snapshots_only", point_in_time_safe=False)
    return run(compare)


@mcp.tool(annotations=LOCAL_READ)
def analyze_wind_series(receipt_id: str, periods_per_year: float, field: str = "close", window: int = 20,
                        annual_risk_free_rate: float = 0.0) -> dict[str, Any]:
    """Compute SMA, momentum, total return, drawdown, volatility and Sharpe from a validated one-security WSD/WSES/WSI receipt or a saved series-row selection derived from one. To analyze filtered/count-limited bars, pass derived_receipt_id; its source fingerprint and selected rows are checked again. Set annualization explicitly for the sampling frequency. Missing/nonpositive prices are rejected. Results describe the observed price series, not a strategy backtest."""
    return run(series_analysis, service.store, receipt_id, field, window, periods_per_year, annual_risk_free_rate)


@mcp.tool(annotations=LOCAL_READ)
def analyze_wind_risk(receipt_id: str, periods_per_year: float, field: str = "close",
                      benchmark_receipt_id: str | None = None, benchmark_field: str = "close",
                      annual_risk_free_rate: float = 0.0, confidence: float = 0.95,
                      alignment: str = "strict") -> dict[str, Any]:
    """Analyze validated Wind price receipts or rebuilt series selections: sample volatility, Sharpe, drawdown dates, down-interval count, empirical VaR and ES. An explicit benchmark receipt adds OLS beta/Jensen alpha, correlation, tracking error and information ratio. Requires >=4 positive finite prices; no missing-value filling. Annualization must be chosen for the input sampling. strict requires identical consecutive return intervals; common_intervals explicitly intersects START+END timestamps after returns are calculated independently, never bridging calendar gaps by joining prices. Standalone metrics still use the full asset sample. VaR/ES describe one input interval and can be negative; ES includes fractional boundary mass. Retains source hashes/options and saves a separate derived receipt. Does not query Wind, certify currency/adjustment equivalence, reproduce proprietary provider metrics or report strategy backtest performance."""
    return run(risk_analysis, service.store, receipt_id, periods_per_year, field, benchmark_receipt_id,
               benchmark_field, annual_risk_free_rate, confidence, alignment)


@mcp.tool(annotations=LOCAL_READ)
def screen_wind_snapshot(receipt_id: str, conditions: list[dict], sort_by: str | None = None,
                         descending: bool = True, limit: int = 100) -> dict[str, Any]:
    """Screen an explicit WSS/WSEE/WSQ receipt universe with AND conditions {field,op,value}. op=gt/gte/lt/lte/eq/ne. Missing inputs are reported; this is not full-market security discovery."""
    return run(screen_snapshot, service.store, receipt_id, conditions, sort_by, descending, limit)


@mcp.tool(annotations=READ)
def screen_wind_universe(plan: dict | None = None, continuation_receipt_id: str | None = None,
                         batches_per_call: int = 2) -> dict[str, Any]:
    """Screen all members of a fixed validated WSET/WEQS receipt, querying WSS in batches of 50. Start with plan={universe_receipt_id,fields,options,conditions,evidence,code_field?,sort_by?,descending?,limit?}; fields and options need documented meanings. Conditions use {field,op,value}. Or resume using only continuation_receipt_id. Processes 1..3 batches per call; status=pending requires continuation, batch_failed requires error review, complete returns globally sorted rows. Never truncates the universe, substitutes missing securities or returns premature rankings. Results do not certify full-market coverage or point-in-time safety."""
    return run(screen_universe, service, plan, continuation_receipt_id, batches_per_call)


@mcp.tool(annotations=LOCAL_READ)
def aggregate_wind_snapshot(receipt_id: str, field: str, operation: str, weight_field: str | None = None) -> dict[str, Any]:
    """Aggregate a validated snapshot using sum/mean/median/min/max/weighted_mean. Missing values are rejected. Caller must establish currency and unit consistency across securities; there is no automatic conversion."""
    return run(aggregate_snapshot, service.store, receipt_id, field, operation, weight_field)


@mcp.tool(annotations=LOCAL_READ)
def combine_economic_series(inputs: dict[str, str], expression: str) -> dict[str, Any]:
    """Derive a fundamental series from alias-to-EDB-receipt inputs and arithmetic + - * /. Uses exact common dates, no fill or automatic unit conversion. Retains input metadata and provenance; observation dates do not certify historical availability."""
    return run(combine_edb, service.store, inputs, expression)


@mcp.resource("windterminal://migration")
def migration_status() -> str:
    return dumps(capabilities())


@mcp.resource("windterminal://usage")
def usage() -> str:
    return (ROOT / "MIGRATION.md").read_text(encoding="utf-8")


@mcp.resource("windterminal://field-notes")
def field_notes() -> str:
    """Selected official UI field definitions with provenance; not the full dictionary."""
    return (ROOT / "verification/wind-api-ui-fields.json").read_text(encoding="utf-8")


@mcp.resource("windterminal://query-recipes")
def query_recipes() -> str:
    """Small verified query examples, their receipts and their semantic limitations."""
    return (ROOT / "references/verified-query-recipes.json").read_text(encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--call", choices=["wind_status", "wind_capabilities", "search_economic_indicator",
                                         "describe_economic_indicators", "query_economic_indicator_data", "query_wind_data", "plan_wind_data_queries",
                                         "call_alice_tool_on_windpy", "read_wind_receipt", "register_economic_indicators", "compare_saved_edb_versions",
                                         "analyze_wind_series", "analyze_wind_risk", "screen_wind_snapshot", "screen_wind_universe", "aggregate_wind_snapshot", "combine_economic_series", "inspect_economic_catalog", "parse_wind_edb_export", "parse_windpy_query", "recognize_wind_entities", "search_wind_fields", "search_wind_query_recipes", "search_wind_bundle_metadata", "search_wind_documentation", "plan_wind_fundamentals"])
    parser.add_argument("--params", default="{}")
    options = parser.parse_args()
    if options.call:
        print(dumps(globals()[options.call](**json.loads(options.params))))
    else:
        mcp.run(transport="stdio")
