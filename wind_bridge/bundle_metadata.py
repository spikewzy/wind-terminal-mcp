"""Read clear bundled metadata; no decryption, private requests, or execution."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET

from .catalog import norm
from .common import ROOT, Problem, envelope, identity, now

APP_ETC = Path(os.environ.get("WIND_TERMINAL_BUNDLE_DIR", "/Applications/Wind API.app/Contents/Resources/etc"))
INDEX = ROOT / "references/wind-bundle-metadata.json"
DAT_SHA256 = "80badb60efd133308681d367760effd172019632250e92fb62ae341056658556"
# A record signature in this exact installed build, not a universal DAT decoder.
DAT_MARKER = bytes.fromhex("000060b2a50a0000000000000004000000ae21243b0000000009000000")
LANGUAGE_SHA256 = {
    "zh-CN": "498bb10e6faab9ae9912cfdd8dcbf0ce15f5e5f9cd16612fa3857e37a28b7c37",
    "en-US": "68e142d9595acb6f7e69a503f436d75051f08131f67b26bd558dd3f8c5105b24",
}
FINANCIAL_ENTITY_TYPES = {"stockBondIndex", "stockIndex", "bondIndex", "fundIndex"}
CONTEXT_RECORD_FIELDS = ("node_id", "name", "category_path", "unresolved_root_node_id",
                         "internal_expression", "labels", "language_id", "record_offset")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_lookup_tables(data):
    text = data.decode("gb18030")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "External/entity XML declarations are not supported")
    root = ET.fromstring(text)
    if root.tag != "lookupTables":
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "Expected lookupTables root")
    tables = []
    for table in root:
        if table.tag != "lookupTable":
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Unexpected lookup table element")
        options = []
        for item in table:
            if item.tag not in {"item", "Customitem"}:
                raise Problem("UNSUPPORTED_METADATA_FORMAT", "Unexpected lookup option element")
            options.append({"kind": item.tag, **item.attrib})
        tables.append({**table.attrib, "options": options})
    return tables


def parse_sector_records(data, mapping_data):
    """Read the vendor's self-described system-sector XML and plain index map."""
    text = data.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "External/entity XML declarations are not supported")
    root = ET.fromstring(text)
    if root.tag != "a":
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "Expected the system-sector root")
    mappings = {}
    index_pairs = []
    for line in mapping_data.decode("utf-8-sig").splitlines():
        pair = line.split(",")
        if len(pair) != 2 or not re.fullmatch(r"[A-Za-z0-9]+\.[A-Za-z0-9]+", pair[0]) or not re.fullmatch(r"[A-Za-z0-9]{16}", pair[1]):
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Invalid index-to-sector mapping record")
        mappings.setdefault(pair[1], []).append(pair[0])
        index_pairs.append({"wind_code": pair[0], "sector_id": pair[1]})
    rows, seen = [], set()

    def walk(parent, ancestors):
        for node in parent:
            attrs = dict(node.attrib)
            sector_id, name = attrs.get("i"), attrs.get("n")
            if node.tag != "s" or not isinstance(name, str) or not name or not isinstance(sector_id, str) or not re.fullmatch(r"[A-Za-z0-9]{16}", sector_id) or sector_id in seen:
                raise Problem("UNSUPPORTED_METADATA_FORMAT", "Invalid or duplicate system-sector record")
            seen.add(sector_id)
            rows.append({"sector_id": sector_id, "name": name, "english_name": attrs.get("e"),
                         "category_path": ancestors, "child_count": len(node), "raw_attributes": attrs,
                         "index_codes": mappings.get(sector_id, [])})
            walk(node, ancestors + [name])

    walk(root, [])
    for pair in index_pairs:
        pair["sector_node_found"] = pair["sector_id"] in seen
    return rows, index_pairs


def parse_language_records(data, language):
    """Read the inspected clear WRES label tables, preserving both text variants."""
    if language not in LANGUAGE_SHA256 or digest(data) != LANGUAGE_SHA256[language]:
        raise Problem("UNRECOGNIZED_METADATA_BUILD", "Language parser requires an inspected file fingerprint")
    if len(data) < 64 or data[:4] != b"WRES":
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "Expected a WRES language resource")
    version, build, count, start, text_start = struct.unpack_from(">IIIII", data, 8)
    if version != 1 or build != 20260226 or start != 64 or text_start != start + count * 12 or text_start > len(data):
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "Invalid language resource index")
    labels = {}
    for n in range(count):
        label_id, offset, first_size, second_size = struct.unpack_from(">IIHH", data, start + n * 12)
        begin = text_start + offset
        if label_id in labels or begin + first_size + second_size > len(data):
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Duplicate label ID or out-of-bounds language text")
        try:
            labels[label_id] = [data[begin:begin + first_size].decode("utf-8"),
                                data[begin + first_size:begin + first_size + second_size].decode("utf-8")]
        except UnicodeError:
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Invalid UTF-8 language text") from None
    return labels


def parse_indicator_records(data):
    if digest(data) != DAT_SHA256:
        raise Problem("UNRECOGNIZED_METADATA_BUILD", "DAT parser is limited to the inspected file fingerprint; re-inspect a changed format")
    rows, offset = [], 0
    while True:
        pos = data.find(DAT_MARKER, offset)
        if pos < 0:
            break
        offset = pos + len(DAT_MARKER)
        candidates = []
        for size in range(1, min(pos - 12, 512) + 1):
            if struct.unpack_from("<I", data, pos - size - 4)[0] != size:
                continue
            try:
                label = data[pos - size:pos].decode("gb18030")
            except UnicodeError:
                continue
            if all(char.isprintable() for char in label):
                candidates.append((size, label))
        if len(candidates) != 1 or pos + 52 > len(data):
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Ambiguous or truncated indicator record", byte_offset=pos)
        size, label = candidates[0]
        node, parent = struct.unpack_from("<II", data, pos + 39)
        group_flag, expr_size = data[pos + 47], struct.unpack_from("<I", data, pos + 48)[0]
        if group_flag not in {0, 1} or expr_size > 5000 or pos + 60 + expr_size > len(data):
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Invalid indicator record bounds", byte_offset=pos)
        expression = data[pos + 52:pos + 52 + expr_size].decode("gb18030")
        if not all(char.isprintable() for char in expression) or bool(expression) == bool(group_flag):
            raise Problem("UNSUPPORTED_METADATA_FORMAT", "Record kind and expression do not agree", byte_offset=pos)
        tail_value, language_id = struct.unpack_from("<II", data, pos + 52 + expr_size)
        rows.append({"name": label, "node_id": node, "parent_node_id": parent,
                     "kind": "group" if group_flag else "indicator", "internal_expression": expression or None,
                     "raw_tail_value": tail_value, "language_id": language_id,
                     "record_offset": pos - size - 4})
    nodes = {row["node_id"]: row for row in rows}
    if len(nodes) != len(rows) or len(rows) != 17778:
        raise Problem("UNSUPPORTED_METADATA_FORMAT", "Unexpected record count or duplicate node IDs")
    for row in rows:
        ancestors, seen, parent = [], {row["node_id"]}, row["parent_node_id"]
        while parent in nodes:
            if parent in seen:
                raise Problem("UNSUPPORTED_METADATA_FORMAT", "Cycle in bundled category records")
            seen.add(parent)
            ancestors.append(nodes[parent]["name"])
            parent = nodes[parent]["parent_node_id"]
        row["category_path"] = list(reversed(ancestors))
        row["unresolved_root_node_id"] = parent
    return rows


def attach_language_labels(records, languages):
    if set(languages["zh-CN"]) != set(languages["en-US"]):
        raise Problem("METADATA_LANGUAGE_MISMATCH", "Chinese and English label IDs differ")
    for row in records:
        label_id = row["language_id"]
        if row["name"] not in languages["zh-CN"].get(label_id, []):
            raise Problem("METADATA_LANGUAGE_MISMATCH", "Indicator label does not match its language ID", node_id=row["node_id"])
        row["labels"] = {language: labels[label_id] for language, labels in languages.items()}
    return records


def import_bundle(destination=INDEX):
    inputs = {"parameters": APP_ETC / "EquityQuery/lookuptables.xml",
              "indicators": APP_ETC / "EquityQuery/excel_chs.dat",
              "language_zh": APP_ETC / "lang/M_ZH-CN_WDF.iml",
              "language_en": APP_ETC / "lang/M_EN-US_WDF.iml",
              "sectors": APP_ETC / "base/SysGrp.xml",
              "index_sectors": APP_ETC / "base/IndexSectors.dat"}
    data = {kind: path.read_bytes() for kind, path in inputs.items()}
    tables = parse_lookup_tables(data["parameters"])
    records = parse_indicator_records(data["indicators"])
    languages = {"zh-CN": parse_language_records(data["language_zh"], "zh-CN"),
                 "en-US": parse_language_records(data["language_en"], "en-US")}
    attach_language_labels(records, languages)
    sectors, index_pairs = parse_sector_records(data["sectors"], data["index_sectors"])
    result = {**identity(), "schema_version": 3, "metadata_source_id": "wind_api_bundle", "imported_at": now(),
              "scope": "Parsed clear bundled metadata records, not the full API field dictionary",
              "inputs": {kind: {"path": str(path), "sha256": digest(data[kind]), "size": len(data[kind])}
                         for kind, path in inputs.items()},
              "counts": {"parameter_tables": len(tables),
                         "parameter_options": sum(item["kind"] == "item" for table in tables for item in table["options"]),
                         "custom_options": sum(item["kind"] == "Customitem" for table in tables for item in table["options"]),
                         "indicators": sum(row["kind"] == "indicator" for row in records),
                         "category_groups": sum(row["kind"] == "group" for row in records),
                         "language_entries_per_locale": {language: len(labels) for language, labels in languages.items()},
                         "indicator_and_group_labels_matched": len(records), "sectors": len(sectors),
                         "index_sector_pairs": len(index_pairs),
                         "index_sector_pairs_matched": sum(pair["sector_node_found"] for pair in index_pairs),
                         "index_sector_pairs_unmatched": sum(not pair["sector_node_found"] for pair in index_pairs)},
              "decoding": {"lookup_tables": "standard XML decoded as GB18030",
                           "indicators": "fingerprint-scoped parsing of length-prefixed clear text and node relationships",
                           "language_labels": "fingerprint-scoped WRES index and UTF-8 text; both variants retained without assuming their purpose",
                           "record_boundary": "record_offset starts at the name length; suffix language ID cross-checked against every Chinese record name",
                           "sectors": "standard UTF-8 XML with vendor-described attributes; plain code/sector map preserves distinct IDs without name-based aliasing",
                           "uninterpreted": "DAT header and raw_tail_value semantics; neither is treated as a WAI ID or WSS field",
                           "decryption_used": False, "network_used": False},
              "tables": tables, "records": records, "sectors": sectors, "index_sector_pairs": index_pairs}
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


@lru_cache(maxsize=2)
def _read_index(path, modified_ns):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_status(snapshot, kinds):
    files = {}
    for kind in kinds:
        info = snapshot["inputs"][kind]
        try:
            matches = digest(Path(info["path"]).read_bytes()) == info["sha256"]
        except OSError:
            matches = None
        files[kind] = {**info, "current_source_matches_snapshot": matches}
    states = [info["current_source_matches_snapshot"] for info in files.values()]
    combined = False if False in states else None if None in states else True
    return {"source_files": files, "current_source_matches_snapshot": combined}


class FinancialCandidateContext:
    """Join WAI alternatives by node ID AND exact label; never produce query fields."""
    def __init__(self, path=INDEX):
        self.nodes = {}
        self.metadata = {"metadata_source_id": "wind_api_bundle", "available": False}
        try:
            snapshot = _read_index(str(path), path.stat().st_mtime_ns)
            self.metadata.update(snapshot_imported_at=snapshot["imported_at"],
                                 **source_status(snapshot, ["indicators", "language_zh", "language_en"]))
            if snapshot.get("schema_version") not in {2, 3} or self.metadata["current_source_matches_snapshot"] is not True:
                self.metadata["reason"] = "snapshot_format_or_current_sources_unverified"
                return
            self.nodes = {str(row["node_id"]): row for row in snapshot["records"] if row["kind"] == "indicator"}
            for row in self.nodes.values():
                if any(key not in row for key in CONTEXT_RECORD_FIELDS) or not isinstance(row["name"], str):
                    raise ValueError("Incomplete candidate context record")
            self.metadata["available"] = True
        except (OSError, ValueError, KeyError, TypeError):
            # Optional discovery metadata must not hide a successful WAI response.
            self.metadata["reason"] = "bundle_index_unavailable_or_invalid"

    def match(self, candidate):
        if not isinstance(candidate.get("type"), str) or candidate["type"] not in FINANCIAL_ENTITY_TYPES:
            return None
        result = {"id": candidate.get("id"), "entity_type": candidate["type"],
                  "windpy_field": None, "windpy_mapping_verified": False, "selection_verified": False}
        if not self.metadata["available"]:
            return {**result, "status": "metadata_unavailable"}
        row = self.nodes.get(str(candidate.get("id")))
        if row is None:
            return {**result, "status": "node_id_not_found"}
        label = candidate.get("entity")
        normalize = lambda text: unicodedata.normalize("NFKC", text).strip().casefold()
        if not isinstance(label, str) or normalize(label) != normalize(row["name"]):
            return {**result, "status": "name_mismatch", "bundle_name": row["name"]}
        return {**result, "status": "node_id_and_name_match", "match_basis": "exact_node_id_and_normalized_name",
                "metadata_source_id": "wind_api_bundle",
                "bundle_record": {key: row[key] for key in CONTEXT_RECORD_FIELDS}}


def search_bundle(question, kind="indicator", limit=20, offset=0, path=INDEX):
    if not isinstance(question, str) or not 1 <= len(question) <= 500 or not norm(question):
        raise Problem("INVALID_PARAMS", "question must be 1..500 nonempty characters")
    if not isinstance(kind, str) or kind not in {"indicator", "parameter", "sector"} or type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
        raise Problem("INVALID_PARAMS", "kind=indicator/parameter/sector, limit=1..50 and offset>=0 are required")
    if not path.exists():
        raise Problem("LOCAL_REFERENCE_NOT_INSTALLED", "Optional Wind software metadata has not been imported on this machine",
                      metadata_source_id="wind_api_bundle", reference_available=False,
                      setup_reference="docs/LOCAL_REFERENCES.md", executed=False)
    snapshot = _read_index(str(path), path.stat().st_mtime_ns)
    tokens = [norm(token) for token in re.split(r"[\s,，;；]+", question) if norm(token)]
    candidates = []
    if kind == "indicator":
        for row in snapshot["records"]:
            if row["kind"] == "indicator":
                candidates.append({**row, "windpy_field": None, "windpy_mapping_verified": False})
    elif kind == "sector":
        candidates = [{**row, "constituents_included": False, "current_entitlement_checked": False,
                       "full_market_coverage_certified": False} for row in snapshot.get("sectors", [])]
    else:
        for table in snapshot["tables"]:
            for item in table["options"] or [None]:
                candidates.append({"table_code": table["code"], "table_name": table["name"],
                                   "datatype": table["datatype"], "table_default_label": table.get("defaultItem"),
                                   "option": item, "api_parameter_name": None, "windpy_mapping_verified": False})
    matches = []
    for row in candidates:
        if kind == "sector":
            texts = [row["sector_id"], row["name"], row.get("english_name"), *row["category_path"], *row["index_codes"]]
        else:
            texts = ([row["name"], row["internal_expression"], str(row["node_id"]), *row["category_path"],
                      *[label for variants in row.get("labels", {}).values() for label in variants]]
                     if kind == "indicator" else [row["table_code"], row["table_name"],
                                                   (row["option"] or {}).get("name", ""), (row["option"] or {}).get("value", "")])
        if all(token in " ".join(norm(text) for text in texts if text) for token in tokens):
            exact = norm(question) == norm(row["table_name"] if kind == "parameter" else row["name"])
            matches.append((not exact, row))
    matches.sort(key=lambda item: item[0])
    primary = {"indicator": "indicators", "parameter": "parameters", "sector": "sectors"}[kind]
    input_meta = snapshot["inputs"][primary]
    related = {"indicator": ("indicators", "language_zh", "language_en"), "parameter": ("parameters",), "sector": ("sectors", "index_sectors")}[kind]
    kinds = [key for key in related if key in snapshot["inputs"]]
    return envelope(kind=kind, results=[row for _, row in matches[offset:offset + limit]],
                    total_matches=len(matches), next_offset=offset + limit if offset + limit < len(matches) else None,
                    metadata_source_id="wind_api_bundle", snapshot_imported_at=snapshot["imported_at"],
                    source_file=input_meta, **source_status(snapshot, kinds),
                    catalog_counts=snapshot["counts"], full_wind_catalog=False, executed=False,
                    note="Bundle metadata is discovery evidence. Sector IDs and index codes remain separate, and metadata does not prove constituent coverage or historical availability. Indicator expressions, node IDs and lookup values are not automatic WindPy fields/options. No automatic selection, value scaling, macro execution, source inference or entitlement claim.")
