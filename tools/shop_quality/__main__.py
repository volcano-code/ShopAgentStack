from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .compare import compare_reports
from .contracts import ContractError, load_json, load_jsonl, require, sha256
from .evaluator import apply_gates, evaluate
from .reporting import atomic_write, write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ShopAgentStack offline evaluation. Never calls a model or business API.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("evaluate")
    for key in ("cases", "runs", "manifest", "out"):
        run.add_argument(f"--{key}", type=Path, required=True)
    run.add_argument("--gates", type=Path)
    run.add_argument("--split", choices=("dev", "heldout"), default="heldout")
    run.add_argument("--ks", default="1,3,5")
    run.add_argument("--allow-fixture", action="store_true", help="Explicitly allow evaluator fixtures, not project benchmark evidence")
    comp = commands.add_parser("compare")
    comp.add_argument("--baseline", type=Path, required=True)
    comp.add_argument("--candidate", type=Path, required=True)
    comp.add_argument("--out", type=Path, required=True)
    comp.add_argument("--metric", default="ndcg@5")
    comp.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            result = compare_reports(load_json(args.baseline), load_json(args.candidate), metric=args.metric, samples=args.samples)
            atomic_write(args.out, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            print("Comparison written; read its source_kind and limitations before making claims.")
            return 0
        manifest = load_json(args.manifest)
        require(isinstance(manifest, dict), "manifest must be an object")
        require(manifest.get("source_kind") != "fixture" or args.allow_fixture,
                "fixture input refused: use --allow-fixture only for testing the evaluator")
        try:
            ks = tuple(int(value) for value in args.ks.split(","))
        except ValueError as exc:
            raise ContractError("ks must be comma-separated integers") from exc
        report = evaluate(load_jsonl(args.cases), load_jsonl(args.runs), manifest,
                          sha256(args.cases), split=args.split, ks=ks)
        report["input_hashes"] = {"cases": sha256(args.cases), "runs": sha256(args.runs), "manifest": sha256(args.manifest)}
        if args.gates:
            report["input_hashes"]["gates"] = sha256(args.gates)
            report["gates"] = apply_gates(report, load_json(args.gates))
        write_reports(report, args.out)
        gate = "NOT RUN" if report["gates"] is None else ("PASS" if report["gates"]["passed"] else "FAIL")
        print(f"scope={report['claim_scope']} cases={report['dataset_cases']} gate={gate}")
        return int(report["gates"] is not None and not report["gates"]["passed"])
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
