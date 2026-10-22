"""M2.7 fail-closed *human review* gate, not a deployment tool.

Consumes independently fetched GitHub workflow summaries for ONE exact PR
head and binds them to the locally inspected checkout/git tree. It can only
approve review of a release candidate; it cannot merge, deploy, modify DBs,
approve refunds, or claim DeepSeek accuracy. Never reads API keys.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

EXPECTED = (
    "Shop quality", "Workspace experience", "Observability integration",
    "Business E2E", "Business tracing E2E", "Hybrid retrieval E2E",
    "Admin review UI", "Local demo smoke", "Demo seed import",
    "Demo cold recovery",
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = "volcano-code/ShopAgentStack"
MAX_INPUT = 32768
NEEDED_TRACKED = frozenset({
    "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore",
    "apps/web/package-lock.json", "services/agent/requirements.lock",
    "services/commerce/pom.xml", "docs/public-release.md",
    ".github/workflows/shop-quality.yml", ".github/workflows/business-e2e.yml",
    ".github/workflows/hybrid-retrieval-e2e.yml",
})


def fail(ok, error):
    if not ok:
        raise ValueError(error)


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            fail(key not in out, "duplicate key")
            out[key] = value
        return out
    def invalid_constant(_):
        raise ValueError("nonfinite JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def prohibited_file(path):
    # Only literal tracked paths are analyzed; do not open or print file data.
    parts = path.split("/")
    base = parts[-1].lower()
    if any(x in parts for x in (".local", "node_modules", "secrets", "credentials", "backups")):
        return True
    if base == ".env" or (base.startswith(".env.") and not base.endswith(".example")):
        return True
    return base.endswith((".pem", ".p12", ".pfx", ".sqlite", ".sqlite3",
                          ".db", ".key", ".log"))


def validate(payload, *, checkout, tree, parents, tracked):
    fail(type(payload) is dict and set(payload) == {"schema", "source", "workflows"},
         "invalid release evidence schema")
    fail(payload["schema"] == "shopagent-m27-release-review/v1",
         "unrecognized release evidence")
    source = payload["source"]
    fail(type(source) is dict and set(source) == {
        "repository", "head_sha", "base_sha", "draft"
    }, "missing source provenance")
    fail(source["repository"] == REPOSITORY and source["draft"] is True,
         "unapproved repository or non-draft candidate")
    head, base = source["head_sha"], source["base_sha"]
    fail(type(head) is str and HEX40.fullmatch(head) and
         type(base) is str and HEX40.fullmatch(base),
         "malformed exact head or base SHA")
    fail(type(checkout) is str and HEX40.fullmatch(checkout) and
         type(tree) is str and HEX40.fullmatch(tree), "unknown checkout identity")
    fail(type(parents) in (list, tuple) and all(type(p) is str and HEX40.fullmatch(p) for p in parents)
         and (checkout == head or head in parents), "checkout does not contain PR head")
    fail(type(tracked) in (set, frozenset) and NEEDED_TRACKED <= tracked,
         "required locked source or release docs missing")
    fail(not any(prohibited_file(f) for f in tracked), "tracked credential or runtime file")
    reports = payload["workflows"]
    fail(type(reports) is dict and set(reports) == set(EXPECTED),
         "missing/unknown required workflow")
    safe = []
    run_ids = set()
    for name in EXPECTED:
        item = reports[name]
        fail(type(item) is dict and set(item) == {
            "head_sha", "run_id", "run_attempt", "status", "conclusion"
        }, "invalid workflow record")
        fail(item["head_sha"] == head and item["status"] == "completed"
             and item["conclusion"] == "success", "stale/failed workflow")
        run_id, attempt = item["run_id"], item["run_attempt"]
        fail(type(run_id) is int and run_id > 0 and
             type(attempt) is int and 1 <= attempt <= 100 and
             run_id not in run_ids, "duplicate or invalid workflow attempt")
        run_ids.add(run_id)
        safe.append({"workflow":name, "run_id":run_id, "attempt":attempt,
                     "result":"success"})
    return {
        "schema":"shopagent-m27-release-review/v1",
        "decision":"ready_for_human_review_only",
        "source":{"head_sha":head,"base_sha":base,
                  "checkout_sha":checkout,"tree_sha":tree},
        "checks":safe,
        "required_workflow_count":len(EXPECTED),
        "production_deployment_approved":False,
        "main_merged":False,
        "real_deepseek_accuracy_verified":False,
        "real_payment_verified":False,
        "data_migration_compatibility_verified":False,
        "signed_provenance_verified":False,
        "limitations":[
            "External workflow summaries are user-supplied; this tool cannot authenticate GitHub API provenance.",
            "CI success is not a production capacity, paid-LLM or security audit certification.",
            "A human must review the diff, any intermittent failures, credentials and upgrade/rollback plan.",
        ]
    }


def checkout_state(root):
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, timeout=10
        ).strip()
    checkout = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    ancestry = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    fail(ancestry and ancestry[0] == checkout, "invalid checkout ancestry")
    fail(not git("status", "--porcelain", "--untracked-files=all"),
         "dirty release candidate checkout")
    tracked = set(git("ls-files").splitlines())
    return checkout, tree, ancestry[1:], tracked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        fail(args.evidence.is_file() and not args.evidence.is_symlink()
             and 0 < args.evidence.stat().st_size <= MAX_INPUT, "missing input evidence")
        fail(args.out.parent.is_dir() and not args.out.exists()
             and not args.out.is_symlink(), "release evidence output exists")
        record = strict_json(args.evidence.read_text(encoding="utf-8"))
        candidate = validate(record, **dict(zip(
            ("checkout", "tree", "parents", "tracked"), checkout_state(args.repo))))
        fd = os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(candidate, file, sort_keys=True, indent=2, ensure_ascii=False)
            file.write("\n")
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError,
            subprocess.SubprocessError):
        print("Release review refused: missing/invalid provenance or incomplete workflow evidence",
              file=sys.stderr)
        return 2
    print(json.dumps({"review_ready":True,"production_deployment_approved":False,
                      "head_sha":candidate["source"]["head_sha"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
