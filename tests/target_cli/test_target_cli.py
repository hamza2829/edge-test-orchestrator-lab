import json
import subprocess

import pytest


def run_target(*args):
    return subprocess.run(["build/target_sim", *args], capture_output=True, text=True, timeout=10, check=False)


def test_periodic_loop_emits_every_cycle():
    result = run_target("--period-ms", "1", "--cycles", "4")
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["event"] == "scheduler"
    assert "SCHED_FIFO" in events[0]["message"]
    if events[0]["policy"] == "normal":
        assert "using normal SCHED_OTHER" in events[0]["message"]
    samples = [event for event in events if event["event"] == "sample"]
    assert [sample["cycle"] for sample in samples] == [0, 1, 2, 3]
    assert all(sample["latency_us"] >= 0 for sample in samples)
    assert samples[0]["jitter_us"] == 0
    assert events[-1]["cycles"] == 4
    assert events[-1]["missed_deadlines"] == sum(sample["deadline_missed"] for sample in samples)


def test_work_longer_than_period_causes_real_misses():
    result = run_target("--period-ms", "1", "--cycles", "3", "--work-ms", "2", "--stress-threads", "1")
    assert result.returncode == 0
    final = json.loads(result.stdout.splitlines()[-1])
    assert final["missed_deadlines"] == 3


@pytest.mark.parametrize("args", [("--period-ms", "0"), ("--unknown", "1"),
                                 ("--cycles", "2", "--cycles", "3"), ("--cycles",)])
def test_invalid_cli_arguments_return_two(args):
    result = run_target(*args)
    assert result.returncode == 2 and "Target error:" in result.stderr
