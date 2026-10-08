"""Offline CLI: raw snapshots and sidecar observations -> private evaluator artifacts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .agent_adapter import adapt
from .contracts import ContractError, load_json, load_jsonl, require, sha256


def write_private(path: Path, value: str) -> None:
    # O_EXCL means existing evidence is never silently replaced.
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("cases", "raw-runs", "observations", "experiment", "prompt-file", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--allow-fixture", action="store_true")
    args = parser.parse_args(argv)
    try:
        exp = load_json(args.experiment)
        require(isinstance(exp, dict), "experiment must be an object")
        require(exp.get("source_kind") != "fixture" or args.allow_fixture, "fixture export requires --allow-fixture")
        rows, manifest, provenance = adapt(load_jsonl(args.cases), load_jsonl(args.raw_runs),
            load_jsonl(args.observations), exp, cases_hash=sha256(args.cases), prompt_hash=sha256(args.prompt_file))
        provenance["input_hashes"] = {name: sha256(path) for name, path in {
            "raw_runs": args.raw_runs, "observations": args.observations, "experiment": args.experiment,
            "cases": args.cases, "prompt": args.prompt_file}.items()}
        # All validation is done before creation. Do not overwrite a previous experiment.
        args.out.mkdir(mode=0o700, parents=False, exist_ok=False)
        write_private(args.out / "runs.jsonl", "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows))
        write_private(args.out / "provenance.json", json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        # Last file is the readiness marker: a partial IO failure has no ready manifest.
        write_private(args.out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(f"exported={len(rows)} source={manifest['source_kind']} missing_cases={len(provenance['missing_cases'])}")
        return 0
    except (ContractError, OSError, UnicodeError, ValueError):
        # Input errors can contain a user-created filename or field; never echo raw data.
        print("export failed: input contract, provenance, or output-directory check failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
