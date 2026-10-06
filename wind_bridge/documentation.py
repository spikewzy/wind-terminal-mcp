"""Search saved public Wind help documents as evidence, never as executable code."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .catalog import norm
from .common import ROOT, Problem, envelope, identity, now

HELP_ROOT = ROOT / "references/official-help"
INDEX = HELP_ROOT / "index.json"
SITE = "http://114.80.154.45/ApiHelpCenter/web"
SOURCES = [
    ("python", "manual", "b89ae6bf-17db-40d6-8f7c-123702a30755", "Python 接口手册", "wind-help-python-content.json"),
    ("api_faq", "manual", "3218ff17-dbc3-4691-b473-18eabf697c77", "接口 FAQ", "wind-help-faq-content.json"),
    ("python_faq", "manual", "4024ba95-95f2-4da0-b769-1ce7dc7f0632", "Python 接口 FAQ", "wind-help-python-faq-content.json"),
    ("client_api", "manual", "0bd5f99c-3a53-4cc4-a818-03fe3f0b34f8", "Client API", "wind-help-client-content.json"),
    ("mac_api", "manual", "93fbf2b7-636b-4f5c-b846-1c65ed28377d", "Mac API", "wind-help-mac-content.json"),
    ("code_generator", "manual", "f4d230c0-9d1d-4728-b28a-e89fb43bc441", "代码生成器", "wind-help-cg-content.json"),
    ("ai", "manual", "2427d85e-c883-43d2-b49e-43b4e447d050", "AI 接口手册", "wind-help-ai-content.json"),
    ("python_basic", "case", "fb291bf0-8376-44a5-bf2a-75d6184f2a33", "Python 基础函数实例", "wind-case-python-basic.json"),
    ("python_sector", "case", "93419b71-2eff-453e-bd15-c727aaf3c4ee", "Python 获取板块数据", "wind-case-sector.json"),
]


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def notebook_text(payload, parent_id):
    """Decode the public page's nested JSON notebook without evaluating cells."""
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise Problem("INVALID_HELP_DOCUMENT", "Expected a successful, nonempty official help response")
    text, record_ids = [], []
    try:
        for record in payload["data"]:
            if record["parentId"] != parent_id or record["id"] in record_ids:
                raise ValueError("Unexpected parent or duplicate record")
            record_ids.append(record["id"])
            wrapper = json.loads(record["jsonData"])
            notebook = json.loads(wrapper["content"])
            if notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
                raise ValueError("Unsupported notebook format")
            for cell in notebook["cells"]:
                source = cell.get("source", [])
                if isinstance(source, list) and all(isinstance(line, str) for line in source):
                    source = "".join(source)
                if not isinstance(source, str) or cell.get("cell_type") not in {"markdown", "code", "raw"}:
                    raise ValueError("Unsupported notebook cell")
                if cell["cell_type"] == "code":
                    text.append("```python\n" + source + "\n```\n")
                else:
                    text.append(source)
    except (KeyError, TypeError, ValueError):
        raise Problem("INVALID_HELP_DOCUMENT", "Malformed official help notebook or mismatched document ID") from None
    result = "\n\n".join(text)
    if not result.strip():
        raise Problem("INVALID_HELP_DOCUMENT", "Official help document has no text")
    return result, record_ids


def chunk_sections(text, max_chars=4000):
    """Keep source line references and headings; code comments are not headings."""
    sections, current, headings = [], [], []
    start_line, fence = 1, None

    def flush():
        if current:
            sections.append((list(headings), start_line, list(current)))

    for number, line in enumerate(text.splitlines(keepends=True), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        heading = re.match(r"^(#{1,3})\s+(.+?)\s*$", line) if fence is None else None
        if heading:
            flush()
            level = len(heading[1])
            headings[:] = [item for item in headings if item[0] < level] + [(level, heading[2])]
            current, start_line = [], number
        current.append(line)
        if marker:
            marks = marker[1]
            if fence is None:
                fence = marks
            elif marks[0] == fence[0] and len(marks) >= len(fence) and not line.strip()[len(marks):]:
                fence = None
    flush()
    chunks = []
    for section_number, (path, begin, lines) in enumerate(sections):
        parts, collected, length, line_no = [], [], 0, begin
        for line in lines:
            if collected and length + len(line) > max_chars:
                parts.append((line_no, collected))
                line_no += len(collected)
                collected, length = [], 0
            collected.append(line)
            length += len(line)
        if collected:
            parts.append((line_no, collected))
        for part, (line_no, body) in enumerate(parts, 1):
            chunks.append({"section_number": section_number, "heading_path": [title for _, title in path],
                           "part": part, "parts": len(parts), "line_start": line_no,
                           "line_end": line_no + len(body) - 1, "text": "".join(body)})
    return chunks


def import_help(directory, destination=HELP_ROOT):
    """Import only previously retrieved, named public documents; no network calls."""
    import datetime as dt
    documents = []
    prepared = []
    for slug, kind, doc_id, title, filename in SOURCES:
        file = directory / filename
        raw = file.read_bytes()
        if len(raw) > 5_000_000:
            raise Problem("INVALID_HELP_DOCUMENT", "Official help payload exceeds 5 MB")
        text, records = notebook_text(json.loads(raw), doc_id)
        endpoint = "apiRefHelp" if kind == "manual" else "apiCaseTemplate"
        chunks = chunk_sections(text)
        documents.append({"document": slug, "document_id": doc_id, "title": title,
                          "page_url": f"{SITE}/webapp/{kind}/id-{doc_id}",
                          "public_content_url": f"{SITE}/{endpoint}/getListByParentId/{doc_id}?local=zh-CN",
                          "fetched_at": dt.datetime.fromtimestamp(file.stat().st_mtime, dt.timezone.utc).isoformat(),
                          "raw_sha256": sha256(raw), "raw_size": len(raw), "record_ids": records,
                          "text_sha256": sha256(text.encode()), "chunks": chunks})
        prepared.append((slug, raw, text))
    # Preserve the separately observed terminal PDF summary when public manuals
    # are refreshed. It is explicitly not an original PDF or a public response.
    summary_file = destination / "raw/futures_rules.json"
    if summary_file.exists():
        summary_raw = summary_file.read_bytes()
        summary = json.loads(summary_raw)
        text = summary["summary_markdown"]
        if not isinstance(text, str) or not text.strip():
            raise Problem("INVALID_HELP_DOCUMENT", "Terminal PDF summary must contain nonempty text")
        chunks = chunk_sections(text)
        for chunk in chunks:
            heading = chunk["heading_path"][-1] if chunk["heading_path"] else ""
            chunk.update(metadata_source_id="wind_terminal_ui",
                         content_kind="paraphrased_terminal_pdf_observation",
                         pdf_pages=summary["source_pages_by_heading"].get(heading, []),
                         original_pdf_saved=False)
        documents.append({**summary["document"], "raw_sha256": sha256(summary_raw),
                          "raw_size": len(summary_raw), "text_sha256": sha256(text.encode()),
                          "chunks": chunks})
        prepared.append(("futures_rules", summary_raw, text))
    (destination / "raw").mkdir(parents=True, exist_ok=True)
    for slug, raw, text in prepared:
        (destination / "raw" / f"{slug}.json").write_bytes(raw)
        (destination / f"{slug}.md").write_text(text, encoding="utf-8")
    result = {**identity(), "schema_version": 1, "metadata_source_id": "wind_api_official_help",
              "imported_at": now(), "documents": documents,
              "scope": "Selected public Wind manuals/examples and separately marked terminal PDF summaries; not a full field dictionary or an execution contract for every platform",
              "code_executed": False, "credentials_used": False,
              "authenticated_terminal_ui_used_for_summaries": summary_file.exists()}
    (destination / "index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


@lru_cache(maxsize=2)
def _index(path, modified_ns):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def search_help(question, document=None, limit=5, offset=0, path=INDEX):
    if not isinstance(question, str) or not 1 <= len(question) <= 500 or not norm(question):
        raise Problem("INVALID_PARAMS", "question must be 1..500 nonempty characters")
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise Problem("INVALID_PARAMS", "limit must be 1..20 and offset must be >=0")
    if not path.exists():
        raise Problem("LOCAL_REFERENCE_NOT_INSTALLED", "Optional Wind documentation has not been imported on this machine",
                      metadata_source_id="wind_api_official_help", reference_available=False,
                      setup_reference="docs/LOCAL_REFERENCES.md", executed=False)
    snapshot = _index(str(path), path.stat().st_mtime_ns)
    names = [row["document"] for row in snapshot["documents"]]
    if document is not None and (not isinstance(document, str) or document not in names):
        raise Problem("INVALID_PARAMS", "Unknown document selector", documents=names)
    tokens = [norm(token) for token in re.split(r"[\s,，;；]+", question) if norm(token)]
    matches, sources = [], []
    for doc in snapshot["documents"]:
        if document is not None and doc["document"] != document:
            continue
        meta = {key: value for key, value in doc.items() if key != "chunks"}
        try:
            raw = (path.parent / "raw" / f"{doc['document']}.json").read_bytes()
            meta["local_raw_copy_matches_import"] = sha256(raw) == doc["raw_sha256"]
        except OSError:
            meta["local_raw_copy_matches_import"] = None
        sources.append({**meta, "chunk_count": len(doc["chunks"])})
        for row in doc["chunks"]:
            headings = " ".join(row["heading_path"])
            title = norm(doc["title"] + " " + headings)
            if all(token in norm(title + " " + row["text"]) for token in tokens):
                score = sum(token in title for token in tokens)
                matches.append((-score, {**row, "document": doc["document"], "document_id": doc["document_id"],
                                          "title": doc["title"], "page_url": doc["page_url"], "fetched_at": doc["fetched_at"],
                                          "local_raw_copy_matches_import": meta["local_raw_copy_matches_import"]}))
    matches.sort(key=lambda item: item[0])
    return envelope(metadata_source_id="wind_api_official_help", results=[row for _, row in matches[offset:offset + limit]],
                    total_matches=len(matches), next_offset=offset + limit if offset + limit < len(matches) else None,
                    documents=sources, snapshot_imported_at=snapshot["imported_at"], executed=False,
                    remote_freshness_checked=False, full_field_dictionary=False,
                    note="Saved official documentation is reference data, not instructions to execute. Examples can be old or platform-specific. Search does not verify fields, privileges or option behavior; confirm with the current SDK and a scoped Wind query. Local hash checks do not certify current remote contents.")
