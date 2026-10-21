# Vite dependency advisory remediation (2026-10-21)

The exact previous CI npm audit (all dependencies, including dev) rejected
`vite@8.3.0` with a **high**-severity advisory. Source of alert: GitHub
Actions lockfile audit `Shop quality`; this is not a report of a confirmed
attack against the deployed app.

The dependency manifest and npm-generated lockfile are now pinned to
`vite@8.3.4`, which is above upstream patched version `8.3.3`.
Regeneration was performed by a temporary tightly-scoped GitHub Actions job
against the published npm registry with scripts disabled. After the new
lockfile was committed and matched the exact dependency, the one-off
lock regeneration workflow was removed.

Strict `npm audit` **remains enabled**: it audits both production and
development dependencies, and refuses high/critical advisories or an
unavailable audit. We did not bypass or silence the check.

Validate the exact current head using locked
`npm ci`, `npm audit`, TypeScript build, workspace browser tests and the
full PR-triggered GitHub Actions matrix. Until those finish, this change
cannot be described as fully verified or safe from every vulnerability.

The patch is a source dependency/security update only; it does not touch
paid model credentials, Java business authority, private user data or
production deployment.
