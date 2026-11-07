"""Transactional SQLite cloud storage and a durable edge outbox."""

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from .models import Measurement


class ConflictError(ValueError):
    """A run identifier was reused with different measurements."""


def canonical(measurement):
    return json.dumps(measurement.model_dump(), sort_keys=True, separators=(",", ":"))


def percentile(values, quantile):
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _insert(self, table, measurements):
        accepted = 0
        with self.lock, self.connect() as db:
            for measurement in measurements:
                payload = canonical(measurement)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                existing = db.execute(f"SELECT digest FROM {table} WHERE run_id=?", (measurement.run_id,)).fetchone()
                if existing:
                    if existing[0] != digest:
                        raise ConflictError(f"run_id {measurement.run_id} already has different measurements")
                else:
                    db.execute(f"INSERT INTO {table} VALUES (?, ?, ?)", (measurement.run_id, payload, digest))
                    accepted += 1
        return {"accepted": accepted, "duplicates": len(measurements) - accepted}

    def ingest(self, measurements):
        return self._insert("runs", measurements)

    def enqueue(self, measurement):
        return self._insert("outbox", [measurement])

    def pending(self, limit=50):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT payload FROM outbox ORDER BY rowid LIMIT ?", (limit,)).fetchall()
        return [Measurement.model_validate_json(row[0]) for row in rows]

    def acknowledge(self, run_ids):
        with self.lock, self.connect() as db:
            db.executemany("DELETE FROM outbox WHERE run_id=?", [(identifier,) for identifier in run_ids])

    def pending_count(self):
        with self.lock, self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def summary(self, scenario=None):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT payload FROM runs ORDER BY run_id").fetchall()
        runs = [Measurement.model_validate_json(row[0]) for row in rows]
        if scenario is not None:
            runs = [run for run in runs if run.scenario == scenario]
        samples = [sample for run in runs for sample in run.samples]
        latencies = [sample.latency_us for sample in samples]
        misses = sum(sample.deadline_missed for sample in samples)
        return {"runs": len(runs), "sample_count": len(samples),
                "latency_p50_us": percentile(latencies, 0.50),
                "latency_p95_us": percentile(latencies, 0.95),
                "latency_p99_us": percentile(latencies, 0.99),
                "missed_deadlines": misses, "deadline_miss_rate": misses / len(samples) if samples else 0.0,
                "incomplete_runs": sum(run.status != "complete" for run in runs),
                "statuses": {status: sum(run.status == status for run in runs)
                             for status in ("complete", "killed", "timed_out", "failed")}}
