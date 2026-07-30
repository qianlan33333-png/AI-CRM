# ADR: AI-CRM main CI directly owns production promotion

Date: 2026-07-17
Updated: 2026-07-30

## Decision

AI-CRM remains the sole production release source. A successful `CI Fast` run
for a trusted `push` to `main` automatically invokes the production deploy for
that run's immutable `head_sha`. The normal release path is therefore:

1. open a PR and pass its required checks and review;
2. merge the PR into `main`;
3. pass the `CI Fast` run created by that `main` push; and
4. deploy the exact successful `main` SHA to production automatically.

The old manual dispatch inputs (`release_sha`, `validated_id_sha`, and the fixed
production confirmation text) and the cross-repository/id-dev attestation are no
longer part of the AI-CRM deployment trigger. Historical promotion manifests and
their validator remain audit artifacts and are not runtime deployment gates.

## Production boundary

`.github/workflows/promote-production.yml` accepts only a completed `CI Fast`
`workflow_run` whose conclusion is successful, whose head repository is this
repository, whose head branch is `main`, and whose source event is `push`. It
passes that run's exact SHA to the reusable `.github/workflows/deploy.yml`.

The reusable deploy remains fixed to the `production` environment and production
secrets. It still requires the requested SHA to equal the current repository
`main`, serializes releases without cancelling an active deployment, transfers a
verified incremental bundle, runs migrations inside the production transaction,
and commits only after runtime and public exact-SHA health checks succeed.

## Rollback

An in-flight deployment failure restores the exact previous production SHA using
the existing transaction guard. A later rollback is a revert PR merged to
`main`; after its `CI Fast` succeeds, the same automatic path deploys that new
exact SHA. Schema downgrades and unverified backward resets remain forbidden.
