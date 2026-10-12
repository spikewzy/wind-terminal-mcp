"""Durable owner-scoped queue; started work is never automatically replayed."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

from .common import JOB_ID, RelayError, encode


class Queue:
    def __init__(self, path, *, clock=time.time, lease_seconds=120, retention_seconds=86400):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock, self.lease_seconds, self.retention_seconds = clock, lease_seconds, retention_seconds
        with self.connection() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    lease TEXT, lease_until REAL, result TEXT
                );
                CREATE INDEX IF NOT EXISTS queue_state ON jobs(state,created);
                CREATE INDEX IF NOT EXISTS queue_owner ON jobs(owner,state);
                CREATE TABLE IF NOT EXISTS dedupe (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker (
                    id INTEGER PRIMARY KEY CHECK(id=1), seen REAL, mode TEXT
                );
            """)

    @contextlib.contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def maintain(self, con):
        now = self.clock()
        con.execute("UPDATE jobs SET state='uncertain',updated=? WHERE state='running' AND lease_until<?", (now, now))
        con.execute("UPDATE jobs SET state='expired',updated=? WHERE state='queued' AND created<?", (now, now - 1800))
        con.execute("DELETE FROM jobs WHERE state IN ('completed','cancelled','expired','failed') AND updated<?",
                    (now - self.retention_seconds,))

    @staticmethod
    def public(row):
        return {"job_id": row["id"], "state": row["state"], "created_at": row["created"],
                "updated_at": row["updated"], "result": json.loads(row["result"]) if row["result"] else None}

    def submit(self, owner, job_id, payload):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise RelayError("INVALID_JOB_ID", "job_id must be a 32-character lowercase hex ID")
        text = encode(payload).decode()
        with self.connection() as con:
            self.maintain(con)
            existing = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                if existing["owner"] != owner or existing["payload"] != text:
                    raise RelayError("IDEMPOTENCY_CONFLICT", "Job ID already exists with different ownership or parameters", 409)
                return self.public(existing)
            prior = con.execute("SELECT * FROM dedupe WHERE id=?", (job_id,)).fetchone()
            payload_hash = hashlib.sha256(text.encode()).hexdigest()
            if prior:
                if prior["owner"] != owner or prior["payload_hash"] != payload_hash:
                    raise RelayError("IDEMPOTENCY_CONFLICT", "Job ID was already used", 409)
                raise RelayError("JOB_EXPIRED", "Result retention expired; this job will not be executed again", 410)
            count = con.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND state IN ('queued','running','uncertain')", (owner,)).fetchone()[0]
            total = con.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
            if count >= 20 or total >= 100:
                raise RelayError("QUEUE_FULL", "Pending job limit reached", 429)
            now = self.clock()
            con.execute("INSERT INTO jobs(id,owner,payload,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                        (job_id, owner, text, now, now))
            con.execute("INSERT INTO dedupe VALUES(?,?,?)", (job_id, owner, payload_hash))
            return self.public(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def get(self, owner, job_id):
        with self.connection() as con:
            self.maintain(con)
            row = con.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise RelayError("JOB_NOT_FOUND", "No job exists for this client", 404)
            return self.public(row)

    def cancel(self, owner, job_id):
        with self.connection() as con:
            self.maintain(con)
            row = con.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise RelayError("JOB_NOT_FOUND", "No job exists for this client", 404)
            if row["state"] == "queued":
                con.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?", (self.clock(), job_id))
            elif row["state"] not in {"cancelled", "expired"}:
                raise RelayError("ALREADY_STARTED", "Started work is not terminated; retrieve its result instead", 409)
            return self.public(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim(self, mode):
        with self.connection() as con:
            self.maintain(con)
            now = self.clock()
            con.execute("INSERT OR REPLACE INTO worker VALUES(1,?,?)", (now, mode))
            # An expired started job blocks execution until its original result
            # arrives or an operator resolves it after inspecting the host.
            if con.execute("SELECT 1 FROM jobs WHERE state IN ('running','uncertain') LIMIT 1").fetchone():
                return None
            row = con.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            lease = secrets.token_hex(32)
            con.execute("UPDATE jobs SET state='running',lease=?,lease_until=?,updated=? WHERE id=?",
                        (lease, now + self.lease_seconds, now, row["id"]))
            return {"job_id": row["id"], "client_id": row["owner"], "lease_id": lease,
                    "lease_seconds": self.lease_seconds, **json.loads(row["payload"])}

    def heartbeat(self, job_id, lease):
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=? AND lease=?", (job_id, lease)).fetchone()
            if row is None or row["state"] not in {"running", "uncertain"}:
                raise RelayError("LEASE_LOST", "Job is no longer held by this worker", 409)
            now = self.clock()
            con.execute("UPDATE jobs SET state='running',lease_until=?,updated=? WHERE id=?",
                        (now + self.lease_seconds, now, job_id))
            con.execute("UPDATE worker SET seen=? WHERE id=1", (now,))

    def complete(self, job_id, lease, result):
        text = encode(result).decode()
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=? AND lease=?", (job_id, lease)).fetchone()
            if row is None or row["state"] not in {"running", "uncertain", "completed"}:
                raise RelayError("LEASE_LOST", "Job is no longer held by this worker", 409)
            if row["state"] == "completed":
                if row["result"] != text:
                    raise RelayError("RESULT_CONFLICT", "A different result is already saved", 409)
                return
            con.execute("UPDATE jobs SET state='completed',result=?,updated=? WHERE id=?", (text, self.clock(), job_id))

    def resolve_uncertain(self, job_id):
        with self.connection() as con:
            self.maintain(con)
            cursor = con.execute("UPDATE jobs SET state='failed',updated=? WHERE id=? AND state='uncertain'", (self.clock(), job_id))
            if cursor.rowcount != 1:
                raise RelayError("NOT_UNCERTAIN", "Only uncertain jobs can be resolved manually", 409)

    def status(self, owner):
        with self.connection() as con:
            self.maintain(con)
            row = con.execute("SELECT * FROM worker WHERE id=1").fetchone()
            return {"worker_online": bool(row and self.clock() - row["seen"] < 30),
                    "worker_mode": row["mode"] if row else None,
                    "execution_paused": bool(con.execute("SELECT 1 FROM jobs WHERE state='uncertain' LIMIT 1").fetchone()),
                    "own_pending_jobs": con.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND state IN ('queued','running','uncertain')", (owner,)).fetchone()[0]}
