"""Durable buffering with explicit flush, acknowledgement and retry."""

import os
from threading import Lock

import httpx
from fastapi import FastAPI, HTTPException

from .models import Measurement
from .storage import ConflictError, Store


def create_app(db_path, cloud_url="http://cloud:8000", sender=None):
    app = FastAPI(title="Synthetic edge buffer")
    store = Store(db_path)
    flushing = Lock()
    app.state.store = store
    app.state.flush_lock = flushing

    def send(measurements):
        with httpx.Client(timeout=5) as client:
            response = client.post(cloud_url.rstrip("/") + "/measurements/batch",
                                   json={"measurements": [m.model_dump() for m in measurements]})
            response.raise_for_status()
            return response.json()

    @app.get("/health")
    def health():
        return {"status": "ok", "pending": store.pending_count()}

    @app.post("/measurements", status_code=202)
    def receive(body: Measurement):
        try:
            return {**store.enqueue(body), "pending": store.pending_count()}
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/buffer")
    def buffer():
        return {"pending": store.pending_count()}

    @app.post("/flush")
    def flush():
        if not flushing.acquire(blocking=False):
            raise HTTPException(409, "another flush is already running")
        try:
            pending = store.pending()
            if not pending:
                return {"forwarded": 0, "pending": 0}
            try:
                result = (sender or send)(pending)
                if (not isinstance(result, dict) or type(result.get("accepted")) is not int
                        or type(result.get("duplicates")) is not int
                        or min(result["accepted"], result["duplicates"]) < 0
                        or result["accepted"] + result["duplicates"] != len(pending)):
                    raise ValueError("cloud acknowledgement does not match the batch")
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(503, "cloud forwarding failed; measurements remain buffered") from exc
            store.acknowledge([m.run_id for m in pending])
            return {"forwarded": len(pending), "pending": store.pending_count(), **result}
        finally:
            flushing.release()

    return app


app = create_app(os.getenv("EDGE_DB", "data/edge.sqlite"), os.getenv("CLOUD_URL", "http://cloud:8000"))
