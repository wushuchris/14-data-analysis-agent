# Service Operations Data Analysis Agent

Understand changes in service-request resolution times through reproducible analysis and evidence-backed findings.

**Status: typed inputs, data-quality preparation, and SQL-verified resolution summaries, period comparisons, reconciled case-mix decomposition, and workload association, plus bounded planning, execution, and structured finding verification, report assembly, and SVG charts implemented. A provider-neutral model boundary is implemented and tested with simulated responses. A live provider transport and public demo are not yet available.**

## The problem

Harborlight Service Operations is a fictional equipment-support company. Its operations manager asks:

> Resolution times increased this month. Where did the increase come from, and what should we investigate next?

The planned agent answers:
1. How have resolution times and completed-request volumes changed?
2. Which categories and regions contribute to the change?
3. Could case mix or workload associations help explain the pattern?

The report will distinguish slower handling within categories from a larger share of complex requests. It will show sample sizes, exclusions, quality issues, and limitations.

## Control boundary

**The agent proposes analytical work. Application code validates it, computes results, and controls publication.**

The model may select approved tools and explain validated results. It may not execute arbitrary Python or SQL, modify input records, change quality rules, invent evidence, or turn an association into established causation.

## Planned architecture

Dataset validation → bounded analysis plan → approved calculations → evidence verification → charts and report.

See [ARCHITECTURE.md](ARCHITECTURE.md) for contracts, execution limits, and failure handling.
See [DATA_CONTRACT.md](DATA_CONTRACT.md) for the proposed inputs and reporting semantics.
See [EVALUATION.md](EVALUATION.md) for known-answer scenarios and release criteria.

Planned tools: Python, Pydantic, Pandas, SQLite, pytest, Streamlit, and charts generated from validated result tables. Dependencies will be introduced as each component is built.

## Scope and data

The demonstration will use bundled synthetic records for one fictional company, three regions, and three service categories over three full months. No real customer, employee, or business records are needed.

Resolution reports describe **completed requests grouped by resolution month**. They do not measure unresolved backlog, all incoming-request cohorts, or the full customer experience. Workload comparisons use opening dates and region and do not establish causation.

Initial outputs: management summary, verified findings, charts, data-quality notices, execution events, and a structured JSON report.

## Engineering contribution

A governed analytical work product: validated analytical operations produce reproducible metrics with stable evidence IDs; findings and charts retain traceable calculation provenance.

## Build and deployment

GitHub is the source of truth. CI runs deterministic schema, quality, and calculation tests on pushes to main and pull requests. Full analytical evaluations will follow with remaining tools and model integration. Deployment is not configured yet.

The planned Hugging Face deployment will depend on passing tests and evaluations and run only for pushes to main. Pull requests will never deploy. Deployment credentials belong in the GitHub secret `HF_DEPLOY_TOKEN`; inference credentials belong in the Space secret `HF_TOKEN`. No credentials are required for this scaffold.

## Run deterministic tests

Python 3.11 or later:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`prepare_dataset()` returns a typed profile and prepared records. Call `require(scope)` before using records for an analysis. Missing classifications have explicit counts and filtered group views. Blocking request errors release no request records; blocking operations errors release no operations records. Source snapshots are retained separately from public profile output.

## Available analytical tools

- `summarize_resolution(dataset, period, grouping)`: count, mean, and median for completed requests, overall or by category/region.
- `compare_periods(dataset, baseline, comparison, grouping)`: duration changes, percentage changes, and raw count differences.
- `decompose_case_mix(dataset, baseline, comparison)`: symmetric attribution of the mean change to category proportions and within-category mean changes.
- `analyze_workload(dataset, opening_period)`: descriptive Pearson correlations between incoming requests per staffed hour and completed-cohort mean resolution time, using equal-weight region-day pairs.

Summary and comparison tools enforce quality gates and declared resolution coverage. Pandas results must agree with controlled, parameterized in-memory SQL before publication. Medians are calculated independently in SQL using ranked observations.

Results include stable IDs, normalized dataset identity, periods, population/inclusion counts, missing-classification exclusions, duplicate counts, calculation versions, and warnings. IDs are deterministic content identities, not authorization credentials.

Empty populations have undefined duration metrics. Zero baseline duration makes percentage change undefined. Missing comparison groups produce partial results. Unequal period lengths warn that raw count changes are not rate changes.

The decomposition checks that mix and within-category contributions sum to the observed overall mean change. It requires all requests to be classified and each observed category to have support in both periods. Missing support or classifications produce an incomplete result with no attribution terms; the overall comparison remains available.

Example: the known 14 → 26 hour scenario yields +12 hours from mix and 0 from within-category changes. A combined scenario yields +17.2 hours overall: +13.2 from mix and +4 from within-category changes.

These are arithmetic contributions to observed differences, not causal estimates. The method applies to means, not medians.

## Workload association boundaries

Workload analysis selects requests by **opening date**, within declared operations coverage. It pairs each region-day's incoming requests / staffed hours with the mean elapsed resolution time of observed completed requests opened on that region-day. Pair construction is checked independently in Pandas and SQL.

Each region-day has equal weight; request counts are reported rather than used to replicate the workload measurement. Days without an observed completed-request cohort remain unpaired, not zero-duration observations.

Pooled and per-region correlations are reported with pair counts. The default minimum is 5 region-day pairs, configurable by application-owned AssociationPolicy (minimum 3). Smaller samples or constant inputs yield null coefficients. Available coefficients must agree between Pandas and Python's statistics implementation. No significance, causal, or predictive claim is made.

Every result discloses completed-request selection bias, unadjusted case mix and temporal dependence, and potential regional confounding. Missing workload coverage or staffing blocks the tool. Invalid numeric limits and engine disagreement fail explicitly.

## Bounded planning and execution

Three deterministic recipes are available through `deterministic_plan()`: `resolution_change`, `segments`, and `drivers`. Drivers require an explicit opening-date period for workload analysis.

`run_analysis(dataset, plan)` returns a typed run report. `run_analysis_iter()` streams actual lifecycle events followed by that report. An optional application adapter can propose one follow-up plan using computed outcomes, and one rejected plan can receive a correction. No model service is connected yet.

Plans can select only summarize, compare, decompose, and workload calls. The application rejects unknown fields/tools, duplicate calls, invalid dependencies, out-of-coverage periods, and overlapping comparisons before executing any step in that round. Plans cannot change calculation or quality policy.

The run reserves at most six top-level steps across at most two accepted rounds. Blocked and skipped steps still consume reserved slots. Dependencies use zero-based positions across the whole run; application-generated IDs appear in outcomes. Only a completed prerequisite allows its dependent step to execute. Independent steps can continue after another step fails.

Reports distinguish rejected plans, blocked analyses, failed calculations, partial results, and skipped dependencies. Invalid tool outputs are withheld; exception text is replaced by stable error codes. These controls validate computed results; structured finding verification is available below. Reports and SVG charts are available below. The provider-neutral model boundary is available below; a live transport and the public demo remain to be built.

## Structured findings and evidence verification

`build_evidence_catalog(run_report)` projects allowlisted scalar metrics from eligible results in an application-owned run. Each fact records the result and step IDs, dataset identity, metric/group, units, periods, selection rule, samples, denominators, classification exclusions, method, and warnings. It does not expose raw request records or accept arbitrary JSON paths.

A `FindingDraft` specifies an evidence reference, claimed numeric value, and an optional bounded investigation topic. `verify_findings(run_report, {"findings": [...]})` checks each item and returns accepted findings plus sanitized quarantine codes. Unknown references, undefined values, mismatched numbers, duplicate findings, and unsupported topics are withheld. Valid findings survive alongside rejected items.

Numerical observations are rendered by application code from the actual evidence value. Count claims must match exactly; floating-point claims use the calculation tolerances. Drafts cannot supply observation prose, IDs, scope overrides, or replacement limitations. Investigation topics produce explicitly labeled follow-up suggestions, not established explanations. Free-form model interpretation is not accepted in this version.

`deterministic_findings(run_report)` provides up to 20 verified factual findings without inference. A completed FindingsReport means its proposed findings passed verification; it does not mean every analysis completed or that the whole agent is finished. Partial source results retain their status and caveats.

Example: the 14 → 26 hour scenario can publish a +12 hour mean-change observation and a +12 hour arithmetic mix contribution. Claiming +20 hours is quarantined. An incomplete decomposition cannot supply an attribution claim, and an unavailable correlation cannot become zero.

These evidence interfaces trust application-owned run objects. Typed fields and content IDs do not authenticate an uploaded or externally modified report. A future production import path would need provenance/authentication controls.

## Reports and charts

`assemble_report(run_report)` produces an AnalysisReport with a management summary, verified findings, calculation caveats, step outcomes, actual execution events, and evidence-backed chart specifications. Omitted finding drafts use the deterministic fallback. Explicit invalid or empty drafts keep their rejection/empty status; valid charts can still show independent calculation evidence.

`export_report_json(report)` exports aggregate evidence and provenance without raw request records. Report status is complete, partial, or unavailable, with execution and finding statuses retained separately. A blocked analysis or quarantined finding prevents an overall complete status. Completion describes the requested work, not causal validity or statistical significance.

`render_chart_svg(chart)` creates a standalone SVG with an accessible title/description, periods, selection rule, units, sample sizes, and plain-language caveats. Supported charts show mean resolution time, mean changes by group, arithmetic decomposition, and pooled/regional workload correlation. Every plotted scalar retains its evidence reference.

Bar scales include zero; negative changes extend left of zero. Correlations use a fixed -1 to +1 domain. Undefined values display as unavailable and receive no bar. Decomposition components and the observed total are separate bars, not stacked together. Chart specifications and renderers are application-owned.

The chart renderer uses the Python standard library and existing schema dependency. No plotting framework or model call is needed. The interactive demo UI and deployment are later milestones.

## Bounded model adapter

`run_assisted_analysis(dataset, request, transport=None)` orchestrates planning, approved calculations, finding verification, and report assembly. With no transport it uses the deterministic plan and factual findings without inference. An injected application transport implements `complete(ModelRequest) -> ModelResponse`; no provider SDK, network endpoint, credentials, or billing is configured yet.

The adapter sends the actual application JSON schemas, the selected question/periods, aggregate quality counts, readiness flags, and bounded scalar evidence. It sends no raw request records, workload pair tables, original snapshots, row-level issue locations, or prior model text. Findings and follow-ups disclose when the evidence list is truncated.

A separate model-call budget allows at most four calls: initial planning, one possible correction, one possible follow-up, and findings. The execution engine still owns its six-step budget and single shared correction. Request scopes must match the selected periods. Initial planning requires the overall comparison; missing required question analyses keep the final report partial.

Transport failures, refused/truncated responses, oversized output, and malformed proposals have explicit outcomes. Deterministic planning fallback runs only before tool dispatch or retained calculation results. Once work has run, it is preserved and tools are not replayed. Bad findings fall back to deterministic facts when none are valid; mixed valid/invalid findings retain quarantine.

The returned AssistedAnalysis records planning/finding sources, model-call events, fallback codes, rejected attempts, and requested-work coverage. Public audit objects retain no prompts, provider response text, or exception diagnostics.

Default request controls are 2,000 output tokens, a 20-second timeout, 32 KiB responses, 64 evidence facts, and 64 KiB combined input/schema/instructions. The adapter checks byte and call budgets itself; the transport must enforce token limits and network timeout. A synchronous transport that ignores timeout cannot be interrupted by this module.

All model-adapter tests use simulated responses. Live provider compatibility, structured-output translation, cost, and actual timeout behavior must be checked when implementing the transport.

## License

MIT. See [LICENSE](LICENSE).
