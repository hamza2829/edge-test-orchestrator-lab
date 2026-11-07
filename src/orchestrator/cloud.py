"""Cloud HTTP ingestion with idempotent run identifiers and SQLite summaries."""

import os

from fastapi import FastAPI, HTTPException

from .models import Batch
from .storage import ConflictError, Store


def create_app(db_path):
    app = FastAPI(title="Synthetic cloud measurements")
    store = Store(db_path)
    app.state.store = store

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/measurements/batch")
    def batch(body: Batch):
        try:
            return store.ingest(body.measurements)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/summary")
    def summary(scenario: str | None = None):
        return store.summary(scenario)

    return app


app = create_app(os.getenv("CLOUD_DB", "data/cloud.sqlite"))
