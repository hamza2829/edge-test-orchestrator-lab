import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from orchestrator import cli
from orchestrator.models import Scenario
from orchestrator.runner import DockerRunner, LabError, load_scenarios, parse_logs


def logs_for(measurement):
    events = [{"event": "scheduler", "policy": "normal", "message": "using normal SCHED_OTHER scheduling"}]
    events.extend(sample.model_dump() for sample in measurement.samples)
    events.append({"event": "result", "cycles": len(measurement.samples), "period_ms": measurement.period_ms,
                   "missed_deadlines": sum(s.deadline_missed for s in measurement.samples), "scheduler": "normal"})
    return "\n".join(json.dumps(event) for event in events)


def fake_executor(logs, exit_code="0", quota="750000000", timeout=False):
    calls = []
    waits = 0
    def execute(args, **kwargs):
        nonlocal waits
        calls.append(args)
        output = "container-id"
        if args[1] == "logs":
            output = logs
        elif args[1] == "wait":
            waits += 1
            if timeout and waits == 1:
                raise subprocess.TimeoutExpired(args, 1)
            output = exit_code
        elif args[1] == "inspect":
            output = quota
        return SimpleNamespace(returncode=0, stdout=output, stderr="")
    return execute, calls


def test_run_applies_cpu_quota_and_cleans_up(tmp_path, measurement):
    execute, calls = fake_executor(logs_for(measurement))
    result = DockerRunner(executor=execute).run(Scenario(name="baseline", cycles=4), tmp_path)
    assert result.status == "complete" and len(result.samples) == 4
    assert calls[0][calls[0].index("--cpus") + 1] == "0.75"
    assert "--read-only" in calls[0] and "--cap-drop" in calls[0] and "--network" in calls[0]
    assert calls[-1][1:3] == ["rm", "--force"]
    assert json.loads((tmp_path / "baseline.json").read_text())["nano_cpus"] == 750000000


def test_killed_container_retains_partial_measurements(tmp_path, measurement):
    logs = "\n".join(logs_for(measurement).splitlines()[:-1])
    execute, calls = fake_executor(logs, exit_code="137")
    sleeps = []
    result = DockerRunner(executor=execute, sleep=sleeps.append).run(
        Scenario(name="killed", cycles=10000, kill_after_ms=25), tmp_path)
    assert result.status == "killed" and result.exit_code == 137 and len(result.samples) == 4
    assert 0.025 in sleeps and any(call[1] == "kill" for call in calls)


def test_timeout_is_reported_and_container_is_removed(tmp_path, measurement):
    execute, calls = fake_executor(logs_for(measurement), exit_code="137", timeout=True)
    result = DockerRunner(executor=execute).run(Scenario(name="timeout", cycles=4), tmp_path)
    assert result.status == "timed_out" and calls[-1][1] == "rm"


def test_failed_exit_is_not_marked_complete(tmp_path, measurement):
    execute, _ = fake_executor(logs_for(measurement), exit_code="1")
    result = DockerRunner(executor=execute).run(Scenario(name="failed", cycles=4), tmp_path)
    assert result.status == "failed"


def test_cpu_quota_mismatch_fails_but_cleans_up(tmp_path, measurement):
    execute, calls = fake_executor(logs_for(measurement), quota="0")
    with pytest.raises(LabError, match="CPU quota"):
        DockerRunner(executor=execute).run(Scenario(name="baseline", cycles=4), tmp_path)
    assert calls[-1][1] == "rm"


def test_command_errors_and_unchecked_cleanup():
    execute = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable"))
    runner = DockerRunner(executor=execute)
    with pytest.raises(LabError, match="docker run failed: daemon unavailable"):
        runner.command(["run"])
    assert runner.command(["rm"], check=False) == ""
    with pytest.raises(LabError, match="image"):
        DockerRunner(image="--privileged")


def test_wait_for_sample_retries_then_succeeds():
    outputs = iter(["", '{"event":"sample"}'])
    sleeps = []
    runner = DockerRunner(sleep=sleeps.append)
    runner.command = lambda *args, **kwargs: next(outputs)
    runner.wait_for_sample("test")
    assert sleeps == [0.05]


def test_wait_for_sample_has_a_deadline():
    times = iter([0, 11])
    with pytest.raises(LabError, match="startup limit"):
        DockerRunner(clock=lambda: next(times)).wait_for_sample("never-started")


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"event":"unknown"}', '{"event":"scheduler"}', ""])
def test_broken_target_logs(text):
    with pytest.raises(LabError, match="invalid target JSON"):
        parse_logs(text, Scenario(name="broken"), "id", 0, "complete")


def test_final_result_must_match_samples(measurement):
    logs = logs_for(measurement)
    scenario = Scenario(name="baseline", cycles=4)
    with pytest.raises(LabError, match="inconsistent final"):
        parse_logs(logs.replace('"missed_deadlines": 2', '"missed_deadlines": 0'), scenario, "id", 0, "complete")
    with pytest.raises(LabError, match="duplicate final"):
        parse_logs(logs + "\n" + logs.splitlines()[-1], scenario, "id", 0, "complete")
    assert parse_logs("\n" + logs + "\n", scenario, "id", 0, "complete").scheduler == "normal"


@pytest.mark.parametrize("text", ["[", "scenarios: []", "scenarios: []\nscenarios: []", "? [a, b]\n: x",
                                 "scenarios:\n  - name: one\n    cpus: 0"])
def test_bad_scenarios_include_filename(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(LabError, match="bad.yaml: invalid scenarios"):
        load_scenarios(path)


def test_all_scenarios_and_missing_file(root, tmp_path):
    scenarios = load_scenarios(root / "scenarios/lab.yaml")
    assert len(scenarios) == 5
    assert next(s for s in scenarios if s.name == "cpu-stress").stress_threads == 2
    assert next(s for s in scenarios if s.name == "slower-period").period_ms == 20
    with pytest.raises(LabError, match="missing.yaml"):
        load_scenarios(tmp_path / "missing.yaml")


def test_run_scenarios_posts_to_edge_and_flushes(tmp_path, measurement):
    path = tmp_path / "lab.yaml"
    path.write_text("scenarios:\n  - name: baseline\n    cycles: 4\n")
    client = Mock()
    client.post.return_value.json.return_value = {"forwarded": 1, "pending": 0}
    result = cli.run_scenarios(path, tmp_path, "http://edge/", runner=Mock(run=Mock(return_value=measurement)),
                               client=client)
    assert len(result["runs"]) == 1 and result["flush"]["forwarded"] == 1
    assert client.post.call_args_list[0].args == ("http://edge/measurements",)
    assert client.post.call_args_list[1].args == ("http://edge/flush",)
    client.close.assert_not_called()


def test_cli_success_and_failed_status(tmp_path, measurement, monkeypatch):
    result = {"runs": [measurement.model_dump()], "flush": {"forwarded": 1}}
    monkeypatch.setattr(cli, "run_scenarios", lambda *args, **kwargs: result)
    assert cli.main(["lab.yaml"]) == 0
    result["runs"][0]["status"] = "failed"
    assert cli.main(["lab.yaml"]) == 1


def test_client_is_closed_on_network_error(tmp_path, measurement, monkeypatch):
    path = tmp_path / "lab.yaml"
    path.write_text("scenarios:\n  - name: baseline\n")
    client = Mock()
    client.post.side_effect = httpx.ConnectError("edge offline")
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(cli, "DockerRunner", lambda image: Mock(run=Mock(return_value=measurement)))
    assert cli.main([str(path)]) == 1
    client.close.assert_called_once()


def test_buffer_only_defers_cloud_forwarding(tmp_path, measurement):
    path = tmp_path / "lab.yaml"
    path.write_text("scenarios:\n  - name: baseline\n")
    client = Mock()
    result = cli.run_scenarios(path, tmp_path, "http://edge", runner=Mock(run=Mock(return_value=measurement)),
                               client=client, buffer_only=True)
    assert result["flush"]["deferred"] and client.post.call_count == 1
