"""Combine downloaded CI reports into a traceable measurement record."""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path("artifacts")
counts = {}
for name, path in {"cpp_google_test": "cpp/gtest.xml", "target_cli": "cpp/target-cli.xml",
                   "python": "python/junit.xml", "compose_e2e": "e2e/junit.xml"}.items():
    cases = ET.parse(root / path).getroot().findall(".//testcase")
    assert cases and not any(case.find("failure") is not None or case.find("error") is not None
                             or case.find("skipped") is not None for case in cases), path
    counts[name] = len(cases)
coverage = {}
for name, path in {"cpp": "cpp/coverage.xml", "python": "python/coverage.xml"}.items():
    xml = ET.parse(root / path).getroot()
    covered, valid = int(xml.attrib["lines-covered"]), int(xml.attrib["lines-valid"])
    coverage[name] = {"covered": covered, "valid": valid, "line_percent": 100 * covered / valid}
result = {
    "commit": os.environ["GITHUB_SHA"],
    "run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    "tests": counts, "total_tests": sum(counts.values()), "coverage": coverage,
    "e2e_summary": json.loads((root / "e2e/cloud-summary.json").read_text()),
}
(root / "metrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))
with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
    stream.write("## Measured results\n\n```json\n" + json.dumps(result, indent=2) + "\n```\n")
