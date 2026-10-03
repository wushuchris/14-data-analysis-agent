# Architecture

Status: proposed design; analytical behavior is not implemented yet.

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

## Mean-change decomposition

For category g, let p be its share and m its mean resolution time, with periods 0 and 1.

Use the symmetric decomposition:
- Mix contribution: sum((p1 - p0) * (m1 + m0) / 2)
- Within-category contribution: sum((m1 - m0) * (p1 + p0) / 2)

The two contributions sum to the overall mean change, within numerical tolerance. The method splits the interaction symmetrically. Category-level attribution depends on this documented convention.

Require observations for each included category in both periods and disclose population coverage. Otherwise return an incomplete decomposition rather than imputing a missing mean. This decomposition does not apply to medians.

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
