"""Read metadata from Wind EDB's observed Chinese CSV column export format.

Export headers describe the displayed series. They cannot establish whether
frequency conversion, unit conversion, calculations or filling were applied.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re

from .common import Problem, envelope

MAX_BYTES = 4_000_000
HEADERS = {"国家": "country", "指标名称": "name", "英文名称": "english_name",
           "频率": "freq", "单位": "unit", "指标ID": "code",
           "时间区间": "time_range", "来源": "source", "更新时间": "updated_at"}
CODE = re.compile(r"[A-Z]\d{5,12}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def decode_export(payload: bytes):
    """Decode a deliberately supplied file, never replacing undecodable bytes."""
    if not payload or len(payload) > MAX_BYTES:
        raise Problem("INVALID_EDB_EXPORT", "CSV must contain 1..4000000 bytes")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise Problem("INVALID_EDB_EXPORT", "CSV encoding must be UTF-8 or GB18030; no lossy decoding is used")


def parse_export(csv_text, evidence):
    if not isinstance(csv_text, str) or not csv_text or len(csv_text) > MAX_BYTES:
        raise Problem("INVALID_EDB_EXPORT", "Supply decoded CSV text, at most 4000000 UTF-8 bytes")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000:
        raise Problem("INVALID_PARAMS", "Provide 1..2000 characters describing the export's provenance")
    payload = csv_text.encode("utf-8")
    if len(payload) > MAX_BYTES or "\x00" in csv_text:
        raise Problem("INVALID_EDB_EXPORT", "CSV exceeds the byte limit or contains NUL")
    rows = csv.reader(io.StringIO(csv_text.removeprefix("\ufeff")), strict=True)
    headers, width, dates, footer = {}, None, [], None
    present = []
    values_started = False
    try:
        for line_no, row in enumerate(rows, 1):
            if not row or not any(cell.strip() for cell in row):
                continue
            label = row[0].strip()
            if footer is not None:
                raise Problem("INVALID_EDB_EXPORT", "Unexpected content after the source footer", line=line_no)
            if label.startswith(("数据来源：", "数据来源:")) and all(not c.strip() for c in row[1:]):
                footer = label
                continue
            if label in HEADERS and not values_started:
                if label in headers:
                    raise Problem("INVALID_EDB_EXPORT", "Duplicate metadata header", header=label)
                if width is None:
                    width = len(row) - 1
                    if not 1 <= width <= 500:
                        raise Problem("INVALID_EDB_EXPORT", "Export must have 1..500 indicator columns")
                    present = [0] * width
                if len(row) != width + 1:
                    raise Problem("INVALID_EDB_EXPORT", "Metadata column counts differ", line=line_no)
                headers[label] = [cell.strip() for cell in row[1:]]
                continue
            if not DATE.fullmatch(label):
                raise Problem("INVALID_EDB_EXPORT", "Unsupported header or layout; use Simplified Chinese CSV, column layout, ISO dates", line=line_no)
            try:
                dt.date.fromisoformat(label)
            except ValueError:
                raise Problem("INVALID_EDB_EXPORT", "Invalid observation date", line=line_no)
            values_started = True
            if width is None or len(row) != width + 1:
                raise Problem("INVALID_EDB_EXPORT", "Data and metadata column counts differ", line=line_no)
            dates.append(label)
            for i, value in enumerate(row[1:]):
                if value.strip() not in {"", "--", "N/A", "NaN", "nan"}:
                    present[i] += 1
    except csv.Error as exc:
        raise Problem("INVALID_EDB_EXPORT", "Malformed CSV", reason=str(exc))
    if not {"指标ID", "指标名称"} <= headers.keys():
        raise Problem("INVALID_EDB_EXPORT", "Export must include indicator ID and name headers")
    codes, names = headers["指标ID"], headers["指标名称"]
    if any(not CODE.fullmatch(code) for code in codes) or any(not name for name in names):
        raise Problem("INVALID_EDB_EXPORT", "Each column needs an explicit valid EDB code and name")
    if len(set(codes)) != len(codes):
        raise Problem("INVALID_EDB_EXPORT", "Repeated codes may represent transformed series; export each raw indicator once")
    if len(set(dates)) != len(dates) or (dates != sorted(dates) and dates != sorted(dates, reverse=True)):
        raise Problem("INVALID_EDB_EXPORT", "Observation dates must be distinct and ordered")
    candidates = []
    for i, code in enumerate(codes):
        display = {field: headers.get(label, [None] * width)[i] or None for label, field in HEADERS.items()}
        candidates.append({"code": code, "name": names[i], "unit": None, "freq": None, "source": None,
                           "export_metadata": display, "metadata_scope": "export_display_only",
                           "native_series_semantics_verified": False,
                           "displayed_nonblank_observations": present[i]})
    return envelope(metadata_source_id="wind_terminal_ui_export", evidence=evidence,
                    supplied_text_sha256=hashlib.sha256(payload).hexdigest(),
                    origin_independently_verified=False, format="wind_edb_chinese_csv_columns",
                    indicators=candidates, indicator_count=len(candidates),
                    display_observations={"rows": len(dates), "first_date": min(dates) if dates else None,
                                          "last_date": max(dates) if dates else None,
                                          "frequency_inferred": False, "missing_schedule_certified": False},
                    footer=footer, imported=False, executed=False, values_imported=False,
                    native_metadata_promoted=False, windpy_entitlement_verified=False,
                    note="CSV headers describe the displayed export. Check original indicator definitions, untransformed units/frequency, and native values before registering native metadata. Numeric data, source footer and requested/exported date bounds do not certify permissions, full history or point-in-time availability. No formulas are evaluated.")
