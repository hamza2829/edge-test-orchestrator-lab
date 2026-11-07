"""Run YAML scenarios, send measurements to edge, then request a flush."""

import argparse
import json
from pathlib import Path

import httpx

from .runner import DockerRunner, LabError, load_scenarios


def run_scenarios(path, output, edge_url, image="edge-lab-target:ci", runner=None, client=None):
    scenarios = load_scenarios(path)
    runner = runner or DockerRunner(image)
    owns_client = client is None
    client = client or httpx.Client(timeout=10)
    measurements = []
    try:
        for scenario in scenarios:
            measurement = runner.run(scenario, output)
            response = client.post(edge_url.rstrip("/") + "/measurements", json=measurement.model_dump())
            response.raise_for_status()
            measurements.append(measurement)
        response = client.post(edge_url.rstrip("/") + "/flush")
        response.raise_for_status()
        result = {"runs": [m.model_dump() for m in measurements], "flush": response.json()}
        target = Path(output) / "orchestration.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        if owns_client:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run synthetic Docker target scenarios")
    parser.add_argument("scenarios")
    parser.add_argument("--out", default="artifacts/targets")
    parser.add_argument("--edge-url", default="http://127.0.0.1:8001")
    parser.add_argument("--image", default="edge-lab-target:ci")
    args = parser.parse_args(argv)
    try:
        result = run_scenarios(args.scenarios, args.out, args.edge_url, args.image)
        print(json.dumps({"runs": len(result["runs"]), "flush": result["flush"]}, indent=2))
        return 1 if any(m["status"] in {"failed", "timed_out"} for m in result["runs"]) else 0
    except (LabError, OSError, ValueError, httpx.HTTPError) as exc:
        print(f"Orchestrator error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
