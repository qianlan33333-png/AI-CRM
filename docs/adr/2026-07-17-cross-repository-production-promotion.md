# ADR: ID validation evidence and AI-CRM production promotion are separate

Date: 2026-07-17

## Decision

`qianlan333/AI-CRM-ID-refactor` remains the sole owner of
`49.232.57.128` / `id-dev.youcangogogo.com`. AI-CRM does not keep an automatic
test deployment trigger, `TEST_DEPLOY_*` credentials, or a path that can replace
the ID validation checkout.

AI-CRM remains the sole production release source. A manual production promotion
binds two different immutable commits:

1. the current ID-refactor `main` SHA already proven by successful source CI,
   successful ID deployment, and the public id-dev health header; and
2. an AI-CRM candidate commit produced by the reviewed cross-repository port and
   already covered by successful AI-CRM `main` CI.

The binding lives in `docs/releases/production_promotion.json`. Any files added
after the candidate commit must be limited to the reviewed promotion-control
allowlist. An unrelated application, schema, dependency, runtime, or deploy
change invalidates the promotion instead of silently riding the old validation.

## Production boundary

`.github/workflows/deploy.yml` is reusable only from the guarded manual promotion
workflow. It is fixed to the `production` environment, production secrets, an
incremental exact-SHA bundle, and an exact current-production-main check. It has no
push, `workflow_run`, test credential, full-bundle test recovery, or id-dev
mutation path.

The manual confirmation text and production environment remain required. The
promotion validator additionally proves source and target CI evidence before the
production job can receive credentials.

## Rollback

An in-flight deployment failure restores the exact previous production SHA using
the existing transaction guard. A later rollback is a forward AI-CRM commit that
reverts the promoted change and receives fresh CI and promotion evidence; schema
downgrades and unverified backward resets remain forbidden.
