from pathlib import Path

import pytest

from orchestrator.models import Measurement, Sample

ROOT = Path(__file__).resolve().parents[2]


def make_measurement(run_id="run-1", latencies=(0, 10, 20, 30), scenario="baseline", misses=2):
    samples = []
    previous = None
    for index, latency in enumerate(latencies):
        scheduled = (index + 1) * 5000000
        started = scheduled + int(latency * 1000)
        missed = index >= len(latencies) - misses
        samples.append(Sample(cycle=index, scheduled_ns=scheduled, started_ns=started, latency_us=latency,
                              jitter_us=0 if previous is None else (started - previous) / 1000 - 5000,
                              execution_us=6000 if missed else 0, deadline_missed=missed))
        previous = started
    return Measurement(run_id=run_id, scenario=scenario, period_ms=5, expected_cycles=len(samples),
                       cpus=0.75, stress_threads=0, work_ms=0, status="complete", exit_code=0,
                       scheduler="normal", samples=samples)


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def measurement():
    return make_measurement()
