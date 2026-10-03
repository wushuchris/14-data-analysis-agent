# Architecture

Status: intake, resolution summary/comparison, mean-change decomposition, and workload association implemented. Planning, interpretation, verification, and reporting remain planned.

## Stages and contracts

| Stage | Responsibility | Contract |
|---|---|---|
| Intake | Validate tables, record exclusions, assess coverage | DatasetProfile |
| Planning | Choose approved operations and dependencies | AnalysisPlan / AnalysisStep |
| Execution | Run validated Pandas operations and controlled SQL | MetricResult |
| Interpretation | Propose observations, interpretations, limitations | FindingDraft |
| Verification | Validate evidence and numerical references | VerifiedFinding |
| Reporting | Assemble charts, findings, notices, and exports | AnalysisReport |

Application-assigned IDs identify steps, results, findings, and events. Generated prose is never a system identifier.

## Approved operations

- Summarize resolution times and completed volumes.
- Compare two periods.
- Break down results by category or region.
- Decompose mean changes into case-mix and within-category effects.
- Examine workload associations.

Plans specify operation names, supported periods, grouping, and dependencies. Validate the full plan before execution. Require dependencies to reference valid preceding steps. SQL is generated from application-owned templates with bound values and approved identifiers, against in-memory tables. Model-generated Python and SQL are not executed.

Initial budget: six executed analysis steps total across the initial plan and at most one follow-up planning round. One rejected plan may receive one bounded correction; correction does not grant another execution budget.

## Result provenance and publication

Each MetricResult contains a stable ID, value or result table, units, sample size, filters, exclusions, input identity, and calculation method/version.

Numerical report statements are rendered from validated result references. FindingDraft separates computed observation, interpretation, and limitation/follow-up. Verification rejects unknown evidence IDs and claims inconsistent with the referenced result. Narrative grounding will still require evaluation and human review; references alone do not prove an interpretation.

Charts use validated result tables. Their grouping, units, and population must match the report.

## Implemented summary and comparison controls

Summary and comparison tools validate periods against declared resolution coverage and reject overlapping or reversed comparisons. Grouping identifiers come from an application-owned allowlist.

Pandas and SQLite independently calculate group counts, means, and medians. SQL parameters carry period values; identifier interpolation uses only fixed approved expressions. The SQLite connection becomes query-only after population and is closed after calculation. Engine disagreement raises CalculationMismatch rather than releasing a result. Tolerances: relative 1e-10 and absolute 1e-9 hours.

Result identities hash normalized prepared requests, operations, declared coverage, quality profile, and policy; result IDs also include period/grouping and method version. Raw source records are not included in result exports. Input row order does not affect identities when the quality profile is unchanged.

Empty groups retain undefined statistics. Missing classification is disclosed in the selected period's denominator; observed small period groups use the preparation policy's threshold. Count comparisons remain raw counts, with unequal-window warnings.

## Mean-change decomposition

For category g, let p be its share and m its mean resolution time, with periods 0 and 1.

Use the symmetric decomposition:
- Mix contribution: sum((p1 - p0) * (m1 + m0) / 2)
- Within-category contribution: sum((m1 - m0) * (p1 + p0) / 2)

The two contributions sum to the overall mean change, within numerical tolerance. The method splits the interaction symmetrically. Category-level attribution depends on this documented convention.

Require observations for each observed category in both periods and no unclassified requests in either period. Otherwise return an incomplete decomposition rather than imputing a missing mean or silently restricting the population. Incomplete results retain overall and available category comparisons but every attribution term is null.

Category shares use the entire period's completed-request population as denominator. Complete results contain category counts, shares, means, per-category contributions, totals, and a reconciliation residual. Totals use compensated summation and must agree with the overall mean change within the summary calculation tolerances. A disagreement raises CalculationMismatch before returning a result. Result validation also requires complete evidence and category contributions that sum to the totals.

The stable result identity includes the method version and referenced comparison IDs. Warnings preserve tiny groups, unequal period lengths, and completed-request limitations. Every result states that it is arithmetic attribution rather than causation, and that it applies to means rather than medians.

## Workload association

The tool accepts an opening-date period within declared operations coverage and requires the dataset workload gate. It aggregates completed-request durations by opening date and region, then joins the unique operations row on that key.

Each observed region-day pair contains incoming requests, staffed hours, their ratio, completed-cohort sample size, and mean duration. Independent Pandas and fixed SQLite joins/aggregations must agree. SQL uses only application-owned statements, query-only connections after population, and typed prefiltered records.

Pearson correlation is calculated over equal-weight region-day pairs, pooled and for each declared region. Positive rescaling limits numerical overflow; Pandas and Python statistics results must agree within the documented tolerances. Application-owned AssociationPolicy requires at least 5 pairs by default and never permits fewer than 3. Insufficient pairs, constant variables, and numerically unavailable coefficients produce explicit null results. Safe numeric-limit violations or independent-engine disagreement raise errors.

Eligible, paired, and unpaired region-day counts are explicit. Operations days without observed completed cohorts do not acquire invented durations. Pair and request counts remain distinct.

This descriptive analysis does not adjust for category mix, autocorrelation, regional confounding, or completed-request selection/right-censoring. It supplies neither p-values nor confidence intervals. These limitations are required warnings, not discretionary model text. A complete result means a coefficient was computable, not that it is statistically significant or causally valid.

Result identity includes dataset identity, opening period, method version, and correlation policy. Exports contain validated pair tables and aggregate coefficients, not raw request identifiers.

## Failure outcomes

- Invalid plan: reject before execution; one bounded correction.
- Unusable data: block affected analyses and report the reason.
- Unsupported finding: quarantine it while retaining independently valid work.
- Model timeout or malformed output: preserve computed results and produce a deterministic report.
- Exhausted budget: stop and label the report incomplete.
- Missing workload coverage: withhold affected workload conclusions.

Workflow states and result statuses will be typed. Blocked, failed, partial, and completed outcomes remain distinct. Retries apply only to explicitly retryable operational failures.

## Runtime and security

One analyst agent; application-owned workflow and session-isolated run context. No persistent memory is required initially. Data cells are untrusted data, never instructions. Runtime events reflect actual execution. Public traces contain synthetic summaries and omit secrets and raw model prompts.

The first public demo uses approved bundled data. Only bounded summaries and computed results will be supplied to the optional model adapter. No paid inference or additional infrastructure is required for deterministic mode.

## Production upgrades

Before using real business data: authentication, authorization, approved data handling/retention, stronger isolation and resource limits, operational monitoring, audited provider data policies, larger-scale validation, and domain review. A real workload study would need richer timing, staffing, and request-cohort data to support stronger explanations.
