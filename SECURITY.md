# Security and privacy review

Reviewed November 6, 2026 for the public synthetic demonstration. This is an engineering review of the implemented boundaries, not a certification for confidential production data.

## Scope and data flow

The public app accepts only bundled synthetic scenarios and fixed questions/periods. There is no upload, arbitrary prompt, secret-entry, report-import, or arbitrary-code interface. Prepared records remain in application memory. Optional inference receives bounded aggregate quality summaries and computed evidence, not original records, request identifiers, timestamps, workload pair tables, or row-level issue locations.

Aggregate JSON/SVG downloads contain computed evidence and provenance. A downloaded February → March JSON report was parsed and checked recursively for row, credential, and prompt fields; none were present. The matching SVG parsed successfully and contained no script or foreignObject elements. These export checks complement automated validation; do not reinterpret synthetic-only support as approval to upload real data.

## Application authority

- Closed schemas and full-round validation precede dispatch.
- Only fixed analytical operations and SQL templates are executable; model-generated Python/SQL is not run.
- Quality gates block unusable tables; independent arithmetic expectations and SQL/Pandas checks protect calculations.
- Minimum question-specific calls are required before initial dispatch; executed work is never silently replayed.
- Model findings select bounded evidence; application code supplies exact numbers, verifies references, renders observations, and attaches limitations.
- Invalid items are quarantined without publishing raw output. Stable IDs provide traceability, not authentication of externally modified records.
- Escaped SVG and application-rendered text constrain output publication.

## Network and credentials

The HF transport uses a fixed endpoint/model, verified TLS, explicit backend bearer authentication, zero automatic retries, disabled redirects, bounded streaming, and closed connection pools. Tokens, prompts, provider response bodies, identity metadata, and raw exceptions are excluded from public reports and diagnostic events. An unclassified 403 can trigger one read-only identity check; only accepted/rejected/unknown is retained.

GitHub's deployment secret and the Space runtime inference secret serve separate roles. They are configured in their respective secret stores; no secret values were requested or inspected in this review. Deterministic mode requires neither inference credentials nor network access.

## Deployment and resource controls

Pull requests test without deploying. Main pushes must pass deterministic tests, Docker build, health, and non-root checks before sync. Deployment uses an allowlisted bundle, hash/source manifest, fixed existing public Docker Space, CPU Basic hardware checks, and a parent-commit concurrency check. No resources or account settings are created or changed by the sync.

AI requires explicit submission. Four shared process reservations each allow at most three model calls. The allowance is not refunded or replenished by UI actions. Restart/redeploy resets it; it is not a durable monthly billing control. Socket timeouts bound blocking operations, not total wall time.

## Public-repository hygiene

At source commit `dfb65575fe5d52483cf169e8c4f2ec9c152ccbf7`, reviewed all 47 current tracked files and all 149 distinct file versions across 44 commits reachable from main, from the initial commit onward. The listed branch inventory contained main only. Reviewed keyword matches for credential names/prefixes, secrets/tokens, and private-planning indicators. Matches were environment-variable names, fixed error/model identifiers, synthetic test sentinels, exclusion tests, or public documentation. No credential-like value or private planning/source document was found in those file contents.

The subsequent two-file scenario-label correction and this documentation change were separately reviewed before publication. No tracked runtime database, local log/cache, real .env, or secret settings file was found in current or historical changed paths. The placeholder .env.example is intentional. This describes repository file contents and reachable main history; it does not attest to inaccessible/unreachable objects, provider internals, GitHub account settings, or an external dependency vulnerability audit.

## Production upgrades

Before accepting real business data, add authenticated users and role-based authorization, approved ingestion/retention/deletion rules, tenant isolation, reviewed provider data handling, a persistent shared quota and rate limits, a worker-level deadline/cancellation model, operational monitoring, controlled dependency updates, and protected/reviewed release branches. Larger or causal studies need appropriate sampling, cohort definitions, statistical design, and expert review.

For a real secret exposure, revoke/rotate the affected credential and address exposed history; deleting only the current file is insufficient. Never put a secret or private input in a public issue or diagnostic attachment.
