# Architecture

Status: intake, resolution summary/comparison, mean-change decomposition, and workload association implemented. Bounded planning and execution are implemented. Structured numerical findings and evidence verification are implemented. Free-form interpretation, charts, and report assembly remain planned.

## Stages and contracts

| Stage | Responsibility | Contract |
|---|---|---|
| Intake | Validate tables, record exclusions, assess coverage | DatasetProfile |
| Planning | Choose approved operations and dependencies | AnalysisPlan / CompiledStep |
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

Initial budget: six reserved top-level analysis steps total across the initial plan and at most one follow-up planning round. One rejected plan may receive one bounded correction; correction does not grant another execution budget.

## Result provenance and publication

Each MetricResult contains a stable ID, value or result table, units, sample size, filters, exclusions, input identity, and calculation method/version.

Numerical observations are rendered from validated result references. FindingDraft selects an evidence reference, numeric claim, and bounded investigation topic. Verification rejects unknown references and claims inconsistent with the referenced metric. Required limitations come from application evidence. Free-form interpretation remains outside the implemented publication boundary; references alone would not prove an interpretation.

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

Workflow states and result statuses are typed. Blocked, failed, partial, and completed outcomes remain distinct. Retries apply only to explicitly retryable operational failures.

## Implemented execution contract

Closed discriminated call schemas reject extra fields. JSON intake rejects duplicate keys, nonfinite values, and plans larger than 32 KiB. Entire-round validation precedes dispatch, including duplicate detection across rounds. Dependencies reference preceding global zero-based step positions; the application assigns step-001 and subsequent IDs.

The six-slot budget includes blocked and skipped steps; tool_calls separately counts dispatched top-level calls. Internal calculations within comparison/decomposition do not consume additional planner slots. There are at most two accepted rounds and one correction across the whole run. A follow-up adapter receives typed outcomes and remaining slots. If the budget is exhausted, it is not invoked and the run is marked partial.

Each step passes its data-quality gate before dispatch through a fixed application registry. Dependents of partial, blocked, failed, or skipped steps are skipped. Independent work continues. Returned result schemas, dataset identity, grouping, and periods are checked before outcomes expose results. Calculation disagreement and invalid output are failures; missing data blocks analysis. Raw exceptions never enter public events.

Run IDs are unique; result IDs remain deterministic. The iterator yields real lifecycle events and a final RunReport. Abandoning the iterator stops subsequent work; there is no durable persistence or resume guarantee. Deterministic tool failures are not retried. Provider timeouts, model integration, and deterministic narrative fallback remain future work.

## Implemented findings contract

The evidence catalog projects only approved metrics from completed/partial step outcomes. It revalidates result type, dataset identity, requested periods/grouping, nested dataset provenance, and outcome status. It rejects ambiguous references. Each scalar carries a stable evidence ID and the originating result/step, scope, units, sample sizes, population denominators, classification exclusions, result status, method, and warnings.

References identify a top-level result, metric enum, and group. There is no model-controlled path traversal or access to raw records. Null metrics remain in the catalog to support explicit UNDEFINED_METRIC rejection. Available values from partial results remain usable with all caveats; this does not upgrade the source result's completeness.

Draft intake accepts one JSON object with a findings list, at most 20 items and 32 KiB. Duplicate JSON keys, nonfinite JSON, invalid envelopes, and over-budget batches reject the envelope. Individual malformed or unsupported items are quarantined by index and code without exposing their raw text. Valid items continue. Counts require exact equality; other metrics use relative 1e-10 and absolute 1e-9 agreement. Publication always uses the computed value, even if a claim differs within tolerance.

Accepted observations use fixed application wording. Drafts cannot supply arbitrary observations, limitations, units, periods, or IDs. An investigation topic must match the metric and grouping: mix, within-category handling, regional process, or staffing/demand association. These are bounded investigation suggestions, not validated causal interpretations. Core noncausal, completed-cohort, and association limitations are attached by application policy in addition to result warnings.

The deterministic fallback selects up to 20 defined catalog metrics and passes them through the same verifier. FindingsReport status describes verification, independently of RunReport and source result statuses. An empty result means there were no proposed/available findings; it does not imply stable business performance.

The catalog consumes application-owned run objects. It cannot authenticate externally modified persisted records or prove a trusted tool was actually executed using a hash alone. Production report imports would require provenance and access controls. UI formatting, chart consistency, narrative evaluation, and full report publication are later milestones.

## Runtime and security

One analyst agent; application-owned workflow and session-isolated run context. No persistent memory is required initially. Data cells are untrusted data, never instructions. Runtime events reflect actual execution. Public traces contain synthetic summaries and omit secrets and raw model prompts.

The first public demo uses approved bundled data. Only bounded summaries and computed results will be supplied to the optional model adapter. No paid inference or additional infrastructure is required for deterministic mode.

## Production upgrades

Before using real business data: authentication, authorization, approved data handling/retention, stronger isolation and resource limits, operational monitoring, audited provider data policies, larger-scale validation, and domain review. A real workload study would need richer timing, staffing, and request-cohort data to support stronger explanations.
