from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator import edge
from orchestrator.cloud import create_app as create_cloud


def test_edge_to_cloud_with_retry_and_deduplication(tmp_path, measurement):
    cloud = TestClient(create_cloud(tmp_path / "cloud.sqlite"))
    def sender(items):
        response = cloud.post("/measurements/batch", json={"measurements": [m.model_dump() for m in items]})
        response.raise_for_status()
        return response.json()
    client = TestClient(edge.create_app(tmp_path / "edge.sqlite", sender=sender))
    assert cloud.get("/health").json()["status"] == "ok"
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/measurements", json=measurement.model_dump()).status_code == 202
    assert client.get("/buffer").json()["pending"] == 1
    assert client.post("/flush").json()["forwarded"] == 1
    assert client.get("/buffer").json()["pending"] == 0
    assert cloud.get("/summary").json()["sample_count"] == 4
    client.post("/measurements", json=measurement.model_dump())
    assert client.post("/flush").json()["duplicates"] == 1
    assert cloud.get("/summary", params={"scenario": "baseline"}).json()["runs"] == 1
    assert client.post("/flush").json() == {"forwarded": 0, "pending": 0}


@pytest.mark.parametrize("ack", [None, {}, {"accepted": True, "duplicates": 0},
                                {"accepted": -1, "duplicates": 2}, {"accepted": 0, "duplicates": 0}])
def test_bad_cloud_ack_keeps_buffer(tmp_path, measurement, ack):
    client = TestClient(edge.create_app(tmp_path / "edge.sqlite", sender=lambda items: ack))
    client.post("/measurements", json=measurement.model_dump())
    assert client.post("/flush").status_code == 503
    assert client.get("/buffer").json()["pending"] == 1


def test_network_failure_and_restart_keep_buffer(tmp_path, measurement):
    def unavailable(items):
        raise httpx.ConnectError("offline")
    path = tmp_path / "edge.sqlite"
    client = TestClient(edge.create_app(path, sender=unavailable))
    client.post("/measurements", json=measurement.model_dump())
    assert client.post("/flush").status_code == 503
    reopened = TestClient(edge.create_app(path, sender=lambda items: {"accepted": len(items), "duplicates": 0}))
    assert reopened.get("/buffer").json()["pending"] == 1
    assert reopened.post("/flush").json()["pending"] == 0


def test_concurrent_flush_rejected(tmp_path):
    app = edge.create_app(tmp_path / "edge.sqlite")
    app.state.flush_lock.acquire()
    try:
        assert TestClient(app).post("/flush").status_code == 409
    finally:
        app.state.flush_lock.release()


def test_http_sender_uses_cloud_url(tmp_path, measurement, monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"accepted": 1, "duplicates": 0}))
    http_client = httpx.Client(transport=transport)
    factory = Mock(return_value=http_client)
    monkeypatch.setattr(edge.httpx, "Client", factory)
    client = TestClient(edge.create_app(tmp_path / "edge.sqlite", "http://cloud.test/"))
    client.post("/measurements", json=measurement.model_dump())
    assert client.post("/flush").json()["forwarded"] == 1
    factory.assert_called_once_with(timeout=5)


def test_conflicts_and_validation_in_both_services(tmp_path, measurement):
    cloud = TestClient(create_cloud(tmp_path / "cloud.sqlite"))
    client = TestClient(edge.create_app(tmp_path / "edge.sqlite"))
    original = measurement.model_dump()
    changed = {**original, "work_ms": 1}
    cloud.post("/measurements/batch", json={"measurements": [original]})
    assert cloud.post("/measurements/batch", json={"measurements": [changed]}).status_code == 409
    client.post("/measurements", json=original)
    assert client.post("/measurements", json=changed).status_code == 409
    assert client.post("/measurements", json={"scenario": "bad"}).status_code == 422
    assert cloud.post("/measurements/batch", json={"measurements": []}).status_code == 422
