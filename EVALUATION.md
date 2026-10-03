# Evaluation plan

Status: schema, quality, summary/comparison, decomposition, workload, and bounded execution tests implemented. Model behavior and full analytical release scenarios remain planned.

## Known-answer scenarios

1. Stable operation: no material change under a specified threshold.
2. Case-mix change: overall mean changes with unchanged category means.
3. Within-category slowdown: fixed mix, one category becomes slower.
4. Combined change: mix and within-category contributions reconcile to the mean change.
5. Workload association: report relationship without claiming causation.
6. Regional concentration: report contribution and sample sizes.
7. Quality and sparse-data scenarios: duplicates, invalid dates, missing classifications, missing staffing, tiny groups.

## Hand-calculated case-mix oracle

| Category | Hours per request | Baseline count | Comparison count |
|---|---:|---:|---:|
| Routine | 10 | 8 | 2 |
| Complex | 30 | 2 | 8 |

Baseline mean: 14 hours. Comparison mean: 26 hours. Change: +12 hours.
Mix contribution: +12 hours. Within-category contribution: 0 hours.
The summary/comparison and decomposition oracles are implemented: 14 → 26 hours overall, +12 mix contribution, and 0 within-category contribution.

Additional hand-calculated cases:
- Fixed 80% Routine / 20% Complex mix, Routine 10 → 14 hours: +3.2 overall, 0 mix, +3.2 within-category.
- Mix changes from 80% Routine / 20% Complex to 20% Routine / 80% Complex while category means change 10 → 12 and 30 → 36: +17.2 overall, +13.2 mix, +4 within-category.
- Three-category case: +9.4 overall, +8.7 mix, +0.7 within-category.

Also cover improvements, unchanged means, one category, zero durations, scaled oracles, absent categories, missing classifications, empty periods, incomplete-output publication controls, tampered totals, identity, and JSON export.

## Implemented calculation coverage

Hand-computed odd/even medians, repeated values, zero and fractional durations, skewed distributions, period filtering, UTC boundaries, category/region breakdowns, classification exclusions, empty periods, zero-baseline percentages, missing period groups, unequal windows, quality gate enforcement, injected grouping rejection, engine disagreement, stable result identity, JSON null handling, and duplicate/policy provenance.

The SQL/Pandas agreement check is supported by independent arithmetic expectations; engine parity alone does not prove correctness.

## Implemented workload coverage

Independent known-answer positive (+1), negative (-1), and symmetric zero correlations; daily ratios and cohort means; repeated requests without repeated workload weighting; pooled/per-region outputs; insufficient samples; constant inputs; absent observed cohorts; coverage/staffing gates; opening-versus-resolution selection; SQL pair disagreement; independent correlation disagreement; safe numeric limits; required limitations; null exports; and policy/filter-sensitive identities.

The association is descriptive. A computed coefficient is not evidence of statistical significance or causation.

## Implemented planning and execution coverage

Real multi-tool recipes, full-round rejection before dispatch, unknown tools and fields, forged IDs, invalid dependencies, duplicate calls within/across rounds, invalid periods, malformed/oversized JSON, duplicate JSON keys, and nonfinite JSON values.

Control-flow tests cover one correction shared across rounds, one evidence-informed follow-up, shared six-step limits, exhausted-budget short-circuiting, blocked/partial prerequisites, independent continuation, sanitized adapter/tool failures, mismatched output type/dataset/period, actual streaming order, isolated run IDs, and stable result IDs. All use deterministic inputs and no model calls.

## Minimum test package

- 10 success cases.
- 5 edge cases.
- 5 failure cases.
- 3 adversarial cases.
- Additional regression cases from meaningful live failures.

Cover arithmetic correctness, equivalent SQL/Pandas results, invalid plans, unknown evidence IDs, model errors, execution budgets, session isolation, and deterministic fallback. Adversarial cells must not alter instructions, execute code, or bypass publication controls.

## Release gates

- Calculations match independent expected answers within explicit tolerances.
- Mean decomposition reconciles; unsupported categories yield an incomplete result.
- Published numerical observations reference valid computed results.
- Quality failures block affected conclusions.
- Charts and findings agree on values, units, population, and periods.
- Claims respect completed-request and association limitations.
- No model service is needed for deterministic tests.
- Analytical tests and evaluations pass in CI before deployment.
- Final deployed default, alternate, and claimed live-model modes pass production checks.
- Public source and history contain only publishable content; runtime artifacts are untracked.

## Human review rubric

Assess correctness, evidence traceability, appropriate uncertainty, business usefulness, and clear separation of observations from explanations. Reject unsupported causal conclusions or a misleading claim that this measures unresolved backlog.
