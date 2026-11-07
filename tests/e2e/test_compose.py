"""Real containers, real clocks and a real cloud outage on the CI runner."""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def compose(*args):
    result = subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr


def wait_healthy(client, url):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if client.get(url + "/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    pytest.fail(f"service did not become healthy: {url}")


@pytest.mark.e2e
def test_targets_buffer_outage_restart_and_cloud_summary():
    output = Path("artifacts/e2e")
    output.mkdir(parents=True, exist_ok=True)
    edge, cloud = "http://127.0.0.1:8001", "http://127.0.0.1:8002"
    with httpx.Client(timeout=10) as client:
        wait_healthy(client, edge)
        wait_healthy(client, cloud)
        compose("stop", "cloud")
        result = subprocess.run(
            [sys.executable, "-m", "orchestrator.cli", "scenarios/lab.yaml", "--buffer-only",
             "--out", str(output / "targets")], capture_output=True, text=True, timeout=120, check=False)
        (output / "orchestrator.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        runs = json.loads((output / "targets/orchestration.json").read_text())["runs"]
        assert len(runs) == 5
        assert client.get(edge + "/buffer").json()["pending"] == 5
        assert client.post(edge + "/flush").status_code == 503
        compose("restart", "edge")
        wait_healthy(client, edge)
        assert client.get(edge + "/buffer").json()["pending"] == 5
        compose("start", "cloud")
        wait_healthy(client, cloud)
        forwarded = client.post(edge + "/flush")
        assert forwarded.status_code == 200 and forwarded.json()["accepted"] == 5
        assert client.get(edge + "/buffer").json()["pending"] == 0

        by_name = {run["scenario"]: run for run in runs}
        assert all(by_name[name]["status"] == "complete" for name in by_name if name != "killed-container")
        killed = by_name["killed-container"]
        assert killed["status"] == "killed" and killed["exit_code"] == 137
        assert 0 < len(killed["samples"]) < killed["expected_cycles"]
        assert all(sample["deadline_missed"] for sample in by_name["forced-overrun"]["samples"])
        for run in runs:
            evidence = json.loads((output / "targets" / f"{run['scenario']}.json").read_text())
            assert evidence["nano_cpus"] == round(run["cpus"] * 1000000000)
            assert run["scheduler"] == "normal"
            log = (output / "targets" / f"{run['scenario']}.jsonl").read_text()
            assert "SCHED_FIFO unavailable" in log and "using normal SCHED_OTHER" in log
        slower = by_name["slower-period"]["samples"]
        assert slower[1]["scheduled_ns"] - slower[0]["scheduled_ns"] == 20000000

        summary = client.get(cloud + "/summary").json()
        samples = [sample for run in runs for sample in run["samples"]]
        quantiles = statistics.quantiles([sample["latency_us"] for sample in samples], n=100, method="inclusive")
        assert summary["runs"] == 5 and summary["sample_count"] == len(samples)
        assert summary["latency_p50_us"] == pytest.approx(quantiles[49])
        assert summary["latency_p95_us"] == pytest.approx(quantiles[94])
        assert summary["latency_p99_us"] == pytest.approx(quantiles[98])
        assert summary["deadline_miss_rate"] == sum(s["deadline_missed"] for s in samples) / len(samples)
        assert summary["incomplete_runs"] == 1
        assert client.get(cloud + "/summary", params={"scenario": "forced-overrun"}).json()["deadline_miss_rate"] == 1

        for run in runs:
            assert client.post(edge + "/measurements", json=run).status_code == 202
        assert client.post(edge + "/flush").json()["duplicates"] == 5
        assert client.get(cloud + "/summary").json() == summary
        compose("restart", "cloud")
        wait_healthy(client, cloud)
        assert client.get(cloud + "/summary").json() == summary
        (output / "cloud-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        (output / "evidence.json").write_text(json.dumps({
            "target_runs": len(runs), "cloud_outage_buffered": True, "edge_restart_retained_buffer": True,
            "cloud_restart_retained_runs": True, "duplicate_batch_did_not_inflate_samples": True,
            "summary_matches_independent_calculation": True,
        }, indent=2) + "\n", encoding="utf-8")
