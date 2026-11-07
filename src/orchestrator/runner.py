"""Docker CLI execution without a shell or privileged containers."""

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Measurement, Sample, Scenarios


class LabError(ValueError):
    """An actionable scenario or target failure."""


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key, value in loader.construct_pairs(node, deep=True):
        if not isinstance(key, str) or key in result:
            raise LabError(f"duplicate or non-string YAML key: {key}")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_scenarios(path):
    try:
        data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader)
        return Scenarios.model_validate(data).scenarios
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, LabError) as exc:
        raise LabError(f"{path}: invalid scenarios: {exc}") from exc


def parse_logs(logs, scenario, run_id, exit_code, status):
    samples = []
    scheduler = "unknown"
    final = None
    try:
        for line in logs.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("JSON events must be objects")
            if event.get("event") == "scheduler":
                scheduler = event["policy"]
            elif event.get("event") == "sample":
                samples.append(Sample.model_validate(event))
            elif event.get("event") == "result":
                if final is not None:
                    raise ValueError("duplicate final result")
                final = event
            else:
                raise ValueError("unknown event type")
        if status == "complete" and (
                final is None or final.get("cycles") != len(samples)
                or final.get("period_ms") != scenario.period_ms
                or final.get("missed_deadlines") != sum(s.deadline_missed for s in samples)
                or final.get("scheduler") != scheduler):
            raise ValueError("missing or inconsistent final result")
        return Measurement(run_id=run_id, scenario=scenario.name, period_ms=scenario.period_ms,
                           expected_cycles=scenario.cycles, cpus=scenario.cpus,
                           stress_threads=scenario.stress_threads, work_ms=scenario.work_ms,
                           status=status, exit_code=exit_code, scheduler=scheduler, samples=samples)
    except (ValueError, KeyError, TypeError) as exc:
        raise LabError(f"{scenario.name}: invalid target JSON: {exc}") from exc


class DockerRunner:
    def __init__(self, image="edge-lab-target:ci", executor=None, sleep=None, clock=None):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:-]*", image):
            raise LabError("invalid Docker image name")
        self.image = image
        self.executor = executor or subprocess.run
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic

    def command(self, args, timeout=30, check=True):
        result = self.executor(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)
        if check and result.returncode != 0:
            raise LabError(f"docker {args[0]} failed: {result.stderr.strip()[:500]}")
        return result.stdout.strip()

    def wait_for_sample(self, name):
        deadline = self.clock() + 10
        while self.clock() < deadline:
            logs = self.command(["logs", name])
            if any('"sample"' in line for line in logs.splitlines()):
                return
            self.sleep(0.05)
        raise LabError("target did not emit a sample within the startup limit")

    def run(self, scenario, output_dir):
        name = "edge-lab-" + uuid.uuid4().hex[:16]
        target_args = ["--period-ms", str(scenario.period_ms), "--cycles", str(scenario.cycles),
                       "--work-ms", str(scenario.work_ms), "--stress-threads", str(scenario.stress_threads)]
        self.command(["run", "--detach", "--name", name, "--cpus", str(scenario.cpus),
                      "--memory", "128m", "--pids-limit", "64", "--network", "none",
                      "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                      self.image, *target_args])
        status = "complete"
        try:
            if scenario.kill_after_ms is not None:
                self.wait_for_sample(name)
                self.sleep(scenario.kill_after_ms / 1000)
                self.command(["kill", "--signal", "KILL", name])
                status = "killed"
            try:
                exit_code = int(self.command(["wait", name], timeout=scenario.timeout_seconds))
            except subprocess.TimeoutExpired:
                self.command(["kill", name], check=False)
                exit_code = int(self.command(["wait", name], timeout=5))
                status = "timed_out"
            logs = self.command(["logs", name])
            nano_cpus = int(self.command(["inspect", "--format", "{{.HostConfig.NanoCpus}}", name]))
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{scenario.name}.jsonl").write_text(logs + "\n", encoding="utf-8")
            if nano_cpus != round(scenario.cpus * 1000000000):
                raise LabError(f"{scenario.name}: Docker CPU quota differs from the scenario")
            if status == "complete" and exit_code != 0:
                status = "failed"
            measurement = parse_logs(logs, scenario, name, exit_code, status)
            evidence = {"measurement": measurement.model_dump(), "nano_cpus": nano_cpus,
                        "container_exit_code": exit_code}
            (output / f"{scenario.name}.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return measurement
        finally:
            self.command(["rm", "--force", name], check=False)
