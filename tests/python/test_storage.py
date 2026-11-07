import pytest

from orchestrator.storage import ConflictError, Store, percentile


@pytest.mark.parametrize("quantile,expected", [(0, 0), (0.5, 15), (0.95, 28.5), (0.99, 29.7), (1, 30)])
def test_hand_calculated_percentiles(quantile, expected):
    assert percentile([30, 0, 20, 10], quantile) == pytest.approx(expected)


def test_percentile_empty_singleton_and_invalid():
    assert percentile([], 0.5) is None
    assert percentile([7], 0.99) == 7
    with pytest.raises(ValueError):
        percentile([1], 2)


def test_summary_real_calculation(tmp_path, measurement):
    store = Store(tmp_path / "db.sqlite")
    assert store.ingest([measurement]) == {"accepted": 1, "duplicates": 0}
    result = store.summary()
    assert result["sample_count"] == 4 and result["runs"] == 1
    assert result["latency_p50_us"] == 15
    assert result["latency_p95_us"] == pytest.approx(28.5)
    assert result["latency_p99_us"] == pytest.approx(29.7)
    assert result["deadline_miss_rate"] == 0.5
    assert result["missed_deadlines"] == 2


def test_summary_empty_and_filter(tmp_path, measurement):
    store = Store(tmp_path / "db.sqlite")
    assert store.summary()["latency_p50_us"] is None
    assert store.summary()["deadline_miss_rate"] == 0
    store.ingest([measurement])
    assert store.summary("unknown")["runs"] == 0
    assert store.summary("baseline")["runs"] == 1


def test_deduplication_and_conflict_are_atomic(tmp_path, measurement):
    store = Store(tmp_path / "db.sqlite")
    store.ingest([measurement])
    assert store.ingest([measurement])["duplicates"] == 1
    changed = measurement.model_copy(update={"work_ms": 1})
    new = measurement.model_copy(update={"run_id": "new-run"})
    with pytest.raises(ConflictError):
        store.ingest([new, changed])
    assert store.summary()["runs"] == 1


def test_outbox_persists_and_acknowledges(tmp_path, measurement):
    path = tmp_path / "db.sqlite"
    store = Store(path)
    assert store.enqueue(measurement)["accepted"] == 1
    assert store.enqueue(measurement)["duplicates"] == 1
    reopened = Store(path)
    assert reopened.pending_count() == 1 and reopened.pending()[0] == measurement
    reopened.acknowledge([measurement.run_id])
    assert reopened.pending_count() == 0


def test_incomplete_runs_are_separate_from_deadline_misses(tmp_path, measurement):
    store = Store(tmp_path / "db.sqlite")
    partial = measurement.model_copy(update={"status": "killed", "exit_code": 137, "expected_cycles": 100})
    store.ingest([partial])
    result = store.summary()
    assert result["incomplete_runs"] == 1 and result["statuses"]["killed"] == 1
    assert result["deadline_miss_rate"] == 0.5
