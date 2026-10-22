# M2.7 first increment: strict candidate review before any release

This stage is **not** a deployment script. It never alters `main`, creates
a GitHub release, upgrades a database, connects to an API provider, or
claims production availability. It adds a strict, offline candidate
review gate that can be run after all independent CI workflows finish.

## Required evidence

A JSON input with `schema=shopagent-m27-release-review/v1`, a
`source` containing the GitHub repository, **exact candidate head SHA**,
exact base SHA and `draft=true`, and one `workflows` dictionary
containing precisely ten successful, completed exact-head attempts:

- Shop quality
- Workspace experience
- Observability integration
- Business E2E
- Business tracing E2E
- Hybrid retrieval E2E
- Admin review UI
- Local demo smoke
- Demo seed import
- Demo cold recovery

Each record includes `head_sha`, `run_id`, `run_attempt`,
`status=completed`, `conclusion=success`. A failed, skipped, stale,
cancelled, duplicate, missing or extra workflow is rejected. The
locally inspected Git commit must be the candidate head or a PR merge
commit that directly contains it as a parent. The checkout must have a
clean worktree, required lockfiles, licence and release docs, and no
tracked local credential/runtime file extension.

```bash
python -m pytest tools/shop_quality/tests/test_m27_release_review.py -q

# Populate .local/m27-review-input.json with independently verified real
# GitHub Actions results; never paste credentials or raw logs into the file.
python -m tools.shop_quality.m27_release_review \
  --evidence .local/m27-review-input.json \
  --out .local/m27-review-decision.json
```

The output is a 0600 private JSON file and will not overwrite an older
report. A positive report says only
`ready_for_human_review_only`, and always includes
`production_deployment_approved=false`,
`real_deepseek_accuracy_verified=false`,
`data_migration_compatibility_verified=false` and
`signed_provenance_verified=false`.

## Not completed by this code

- **External GitHub API authentication:** a caller supplies status
  records; the offline tool cannot prove their independent authenticity.
  The human reviewer must click through each exact run and inspect
  the downloaded relevant artifacts.
- **Production migration:** backup/recovery tests use disposable stacks;
  there is no validated cross-version live migration with rollback or
  preservation of real customers' data.
- **Automatic deploy:** none is configured. Merging many stacked draft
  PRs requires reviewing their provenance and resolving intermittent
  Hybrid customer-search failures first.
- **Real model certification:** no DeepSeek API Key is configured and
  no independently human-rated real model answers or billing evidence
  exist. The no-Key fixture proves tool/Java state constraints only.

Recommended next release path: freeze a candidate SHA, audit all open
dependency update proposals against locked versions, investigate
intermittent Hybrid failures, perform a fresh independent same-source
end-to-end run, review migration and persistent-volume inventory, write
and rehearse a destructive-action-free rollback plan, then obtain
explicit owner approval for a staged deployment. Do not run
`docker compose down -v` on any persistent demo or production data.
