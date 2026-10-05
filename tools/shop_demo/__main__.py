"""Local demo: build -> init -> up -> status -> stop. No existing environment is adopted."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import runtime
from .state import ROOT, init, validate_state

JAVA_TESTS = ("OrderOwnershipTest,CartPricingTest,AgentOperationTest,RefundServiceTest,"
              "ProductQueryServiceTest,SupportServiceTest,CatalogManagementServiceTest,FulfillmentServiceTest,DemoImportTest,DemoImportAccessTest")


def build() -> None:
    """Build only checkout artifacts; initialized demos use immutable private snapshots."""
    for name in ("mvn", "npm"):
        if shutil.which(name) is None:
            raise ValueError("build requires Maven/JDK17 and Node/npm; see docs/local-demo-m14.md")
    commands = [["mvn", "-B", "-ntp", "-f", "services/commerce/pom.xml", "-Ddocker.skip=true", "-DskipTests=false",
                 "-Dtest=" + JAVA_TESTS, "-Dsurefire.failIfNoSpecifiedTests=false", "-pl", "mall-portal,mall-admin", "-am", "clean", "verify"],
                ["npm", "--prefix", "apps/web", "ci"], ["npm", "--prefix", "apps/web", "run", "build"]]
    for args in commands:
        subprocess.run(args, cwd=ROOT, env=runtime.environment(), check=True, timeout=1200)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("build", help="build the current checkout, not a running demo's files")
    for action in ("init", "up", "status", "stop", "destroy", "seed-preview", "seed-apply"):
        sub = actions.add_parser(action)
        sub.add_argument("--state", type=Path, default=ROOT / ".local/demo")
        if action == "init":
            sub.add_argument("--retrieval", choices=("bm25", "hybrid"), default="bm25")
            sub.add_argument("--port", type=int, default=18030)
            sub.add_argument("--enable-seed-import", action="store_true", help="enable administrator-only synthetic import APIs in this NEW demo")
            mode = sub.add_mutually_exclusive_group()
            mode.add_argument("--fixture", action="store_true", help="explicit non-AI scripted demo; never a real-model fallback")
            mode.add_argument("--allow-model-network", action="store_true", help="allow Agent public egress; no model calls are made by this CLI")
        if action == "seed-preview":
            sub.add_argument("--publish-policies", action="store_true", help="separate publication plan; review all policy text before confirming")
        if action == "seed-apply":
            sub.add_argument("--preview", required=True)
            sub.add_argument("--confirm", required=True)
        if action == "destroy":
            sub.add_argument("--confirm", required=True, help="exact project name; this explicitly deletes this demo's volumes")
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            build()
            return 0
        state = validate_state(ROOT, args.state.absolute())
        if args.action == "init":
            owner = init(ROOT, state, {"retrieval": args.retrieval, "port": args.port,
                         "fixture": args.fixture, "model_network": args.allow_model_network, **({"seed_import": True} if args.enable_seed_import else {})})
            result = {"initialized": True, "project": owner["project"], "credentials_file": str(state / "accounts.json"),
                      "next": "python -m tools.shop_demo up --state " + str(state)}
        elif args.action in ("seed-preview", "seed-apply"):
            from . import seed
            result = seed.preview(state, publish=args.publish_policies) if args.action == "seed-preview" else seed.apply(state, args.preview, args.confirm)
        elif args.action == "destroy":
            result = runtime.destroy(state, args.confirm)
        else:
            result = getattr(runtime, args.action)(state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, runtime.DemoError, subprocess.SubprocessError) as exc:
        # Error text may include no raw service response; subprocess output stays in private logs.
        print("shop-demo refused: " + type(exc).__name__ + "; inspect docs/local-demo-m14.md and private state/logs", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
