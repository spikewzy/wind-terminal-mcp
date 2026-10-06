from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

from .common import RUNTIME, Problem, dumps, identity, now
from .edb_observations import observe_edb_series
from .financial_evidence import statement_reference_checks
from .table_dates import observe_table_dates


class Store:
    def __init__(self, directory: Path = RUNTIME):
        self.directory = Path(directory)
        self.receipts = self.directory / "receipts"
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "wind_terminal_api.sqlite"
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, method TEXT NOT NULL, ok INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS receipts_cache ON receipts(request_key, fetched_at);
                CREATE TABLE IF NOT EXISTS indicators (
                    code TEXT PRIMARY KEY, metadata_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    receipt_id TEXT NOT NULL, code TEXT NOT NULL, observation_date TEXT NOT NULL,
                    value_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    PRIMARY KEY(receipt_id, code, observation_date)
                );
                CREATE TABLE IF NOT EXISTS validations (
                    receipt_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail_json TEXT NOT NULL
                );
            ''')

    def connect(self):
        return sqlite3.connect(self.db, timeout=20)

    @staticmethod
    def key(method, arguments):
        return hashlib.sha256(dumps({**identity(), "method": method, "arguments": arguments}).encode()).hexdigest()

    def save(self, method, arguments, response):
        receipt_id = uuid.uuid4().hex
        receipt = {"receipt_id": receipt_id, **identity(), "source": None, "fetched_at": now(),
                   "method": method, "arguments": arguments, "response": response}
        payload = dumps(receipt).encode()
        path = self.receipts / f"{receipt_id}.json.gz"
        with gzip.open(path, "wb") as f:
            f.write(payload)
        ok = response.get("ok") and response.get("raw", {}).get("ErrorCode", 0) == 0
        with self.connect() as con:
            con.execute("INSERT INTO receipts VALUES(?,?,?,?,?)", (receipt_id, self.key(method, arguments), receipt["fetched_at"], method, int(bool(ok))))
        receipt["sha256"] = hashlib.sha256(payload).hexdigest()
        return receipt

    def read(self, receipt_id):
        if not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
            raise Problem("INVALID_RECEIPT", "Invalid receipt id")
        path = self.receipts / f"{receipt_id}.json.gz"
        if not path.is_file():
            raise Problem("NO_RECEIPT", "Receipt does not exist")
        with gzip.open(path, "rb") as f:
            payload = f.read()
        receipt = json.loads(payload)
        receipt["sha256"] = hashlib.sha256(payload).hexdigest()
        return receipt

    def page(self, receipt_id, offset=0, limit=200):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 2000:
            raise Problem("INVALID_PARAMS", "offset>=0 and limit=1..2000 must be integers")
        receipt = self.read(receipt_id)
        raw = receipt.get("response", {}).get("raw")
        if not raw:
            return receipt
        receipt["raw_dimensions"] = {key: len(raw.get(key) or []) for key in ["Codes", "Fields", "Times", "Data"]}
        columns = raw.get("Data") or []
        if any(not isinstance(column, list) for column in columns):
            raise Problem("UNPAGEABLE_RECEIPT", "Raw data columns are not lists; the saved receipt is unchanged")
        receipt["column_lengths"] = [len(column) for column in columns]
        if receipt["method"] == "edb":
            receipt["edb_series_observations"] = observe_edb_series(raw, receipt["arguments"])
            with self.connect() as con:
                checked = con.execute("SELECT status,detail_json FROM validations WHERE receipt_id=?", (receipt_id,)).fetchone()
            receipt["stored_query_validation"] = ({"status": checked[0], "detail": json.loads(checked[1])}
                                                    if checked else {"status": "not_recorded"})
        if receipt["method"] == "wset":
            receipt["table_date_observations"] = observe_table_dates(raw, receipt["arguments"].get("options", ""))
        compared = statement_reference_checks(receipt["method"], receipt["arguments"], raw)
        if compared is not None:
            receipt["statement_reference_checks"] = compared
        raw["Data"] = [column[offset:offset + limit] for column in columns]
        method = receipt["method"]
        if method in {"wss", "wsee", "wsq"}:
            axis = "Codes"
        elif method in {"wsd", "wses", "wsi", "wst", "edb", "tdays"}:
            axis = "Times"
        else:
            # WSET/news tables keep Times/Codes as request metadata. Their rows
            # are defined by the returned Data columns, not a time-series axis.
            axis = "table_rows"
        if axis in {"Codes", "Times"}:
            raw[axis] = (raw.get(axis) or [])[offset:offset + limit]
        receipt["page"] = {"offset": offset, "limit": limit, "axis": axis,
                           "raw_receipt_unchanged": True}
        return receipt

    def cached(self, method, arguments, max_age_seconds):
        import datetime as dt
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=max_age_seconds)).isoformat()
        with self.connect() as con:
            row = con.execute("SELECT id FROM receipts WHERE request_key=? AND ok=1 AND fetched_at>=? ORDER BY fetched_at DESC LIMIT 1", (self.key(method, arguments), cutoff)).fetchone()
        return self.read(row[0]) if row else None

    def validation(self, receipt_id, status, detail):
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO validations VALUES(?,?,?)", (receipt_id, status, dumps(detail)))

    def put_indicator(self, meta, provenance):
        if not re.fullmatch(r"[A-Z]\d{5,12}", meta.get("code", "")) or not meta.get("name"):
            raise Problem("INVALID_METADATA", "Metadata requires a valid code and name")
        if not provenance.get("metadata_source_id") or not provenance.get("evidence"):
            raise Problem("INVALID_METADATA", "Metadata requires a source id and evidence")
        # Upserts preserve a history of provenance in raw import receipts, not an asserted Wind entitlement.
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO indicators VALUES(?,?,?,?)", (meta["code"], dumps(meta), dumps(provenance), now()))

    def catalog(self):
        with self.connect() as con:
            rows = con.execute("SELECT metadata_json,provenance_json FROM indicators ORDER BY code").fetchall()
        return [{**json.loads(a), "metadata_provenance": json.loads(b)} for a, b in rows]

    def indicator(self, code):
        return next((r for r in self.catalog() if r["code"] == code), None)

    def save_observations(self, receipt, metrics):
        with self.connect() as con:
            for metric in metrics:
                for day, value in zip(metric["date"], metric["value"]):
                    con.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)", (receipt["receipt_id"], metric["meta"]["code"], day, dumps(value), receipt["fetched_at"]))

    def revisions(self, code, begin, end):
        with self.connect() as con:
            rows = con.execute("SELECT observation_date,value_json,fetched_at,receipt_id FROM observations WHERE code=? AND observation_date BETWEEN ? AND ? ORDER BY observation_date,fetched_at", (code, begin, end)).fetchall()
        grouped = {}
        for day, value, fetched, receipt in rows:
            grouped.setdefault(day, []).append({"value": json.loads(value), "fetched_at": fetched, "receipt_id": receipt})
        return [{"date": day, "versions": versions} for day, versions in grouped.items()
                if len({dumps(v["value"]) for v in versions}) > 1]
