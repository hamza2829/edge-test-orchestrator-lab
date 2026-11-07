import pytest
from pydantic import ValidationError

from orchestrator.models import Batch, Measurement, Sample, Scenario, Scenarios


@pytest.mark.parametrize("field,value", [("name", "bad name"), ("period_ms", 0), ("period_ms", 1001),
                                      ("cycles", 1), ("cycles", 10001), ("cpus", 0), ("cpus", 9),
                                      ("cpus", float("nan")), ("work_ms", -1), ("stress_threads", 5),
                                      ("kill_after_ms", 0), ("timeout_seconds", 1), ("extra", "unknown")])
def test_scenario_limits(field, value):
    data = {"name": "test", field: value}
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_duplicate_scenarios_and_empty_batch():
    with pytest.raises(ValidationError, match="unique"):
        Scenarios(scenarios=[Scenario(name="one"), Scenario(name="one")])
    with pytest.raises(ValidationError):
        Batch(measurements=[])


@pytest.mark.parametrize("field,value", [("latency_us", -1), ("jitter_us", float("inf")),
                                      ("execution_us", -1), ("deadline_missed", "yes"),
                                      ("latency_us", 25), ("started_ns", 0)])
def test_invalid_sample(measurement, field, value):
    data = measurement.samples[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Sample.model_validate(data)


def test_complete_run_requires_cycles_and_success(measurement):
    for field, value in [("expected_cycles", 3), ("expected_cycles", 5), ("exit_code", 1)]:
        data = measurement.model_dump()
        data[field] = value
        with pytest.raises(ValidationError):
            Measurement.model_validate(data)


def test_noncontiguous_cycles_rejected(measurement):
    data = measurement.model_dump()
    data["samples"][1]["cycle"] = 3
    with pytest.raises(ValidationError, match="contiguous"):
        Measurement.model_validate(data)


def test_incorrect_jitter_rejected(measurement):
    data = measurement.model_dump()
    data["samples"][1]["jitter_us"] = 0
    with pytest.raises(ValidationError, match="jitter"):
        Measurement.model_validate(data)


def test_release_period_is_checked(measurement):
    data = measurement.model_dump()
    sample = data["samples"][1]
    sample["scheduled_ns"] += 1000
    sample["started_ns"] += 1000
    sample["jitter_us"] += 1
    with pytest.raises(ValidationError, match="scheduled releases"):
        Measurement.model_validate(data)


def test_killed_run_can_have_partial_samples(measurement):
    data = measurement.model_dump()
    data.update(status="killed", expected_cycles=100, exit_code=137)
    assert len(Measurement.model_validate(data).samples) == 4


def test_signed_jitter_for_catch_up(measurement):
    data = measurement.model_dump()
    sample = data["samples"][2]
    sample["started_ns"] = sample["scheduled_ns"]
    sample["latency_us"] = 0
    sample["jitter_us"] = -10
    data["samples"][3]["jitter_us"] = 30
    assert Measurement.model_validate(data).samples[2].jitter_us == -10
