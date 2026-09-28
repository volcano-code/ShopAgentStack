"""Fail closed when an upstream test command succeeds but produces no executed tests."""
from __future__ import annotations
import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(paths: list[Path]) -> dict[str, int]:
    result = {"files": 0, "tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for path in paths:
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
        result["files"] += 1
        result["tests"] += len(cases)
        for case in cases:
            result["failures"] += case.find("failure") is not None
            result["errors"] += case.find("error") is not None
            result["skipped"] += case.find("skipped") is not None
    result["executed"] = result["tests"] - result["skipped"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pattern", default="**/TEST-*.xml")
    args = parser.parse_args()
    try:
        summary = summarize(sorted(args.root.glob(args.pattern)))
    except (ET.ParseError, OSError) as exc:
        print(f"invalid JUnit report: {type(exc).__name__}")
        return 2
    print(json.dumps(summary))
    return int(summary["executed"] == 0 or summary["failures"] > 0 or summary["errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
