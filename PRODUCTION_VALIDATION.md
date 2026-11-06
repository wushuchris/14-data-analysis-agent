# Production validation

## Reviewed version and scope

November 6, 2026. Alternate browser checks used deployed source `dfb65575fe5d52483cf169e8c4f2ec9c152ccbf7`. The analytical code is unchanged by the subsequent scenario-setup label correction. The earlier complete live AI check used `b9cf18f136cdace39e1f4ece65252436647e2ebe`; EVALUATION.md records its two model calls, complete three-tool plan, and verified +16 hour finding.

Checks below used the public Hugging Face Docker Space with bundled synthetic data. Alternate checks used deterministic mode, with no inference calls. This is a portfolio demonstration release review, not authorization for real business data.

| Production path | Observed result |
|---|---|
| Fresh default | Running Space; complete report; 16 → 32 hours, +16 overall, +12 mix, +4 within |
| More complex requests | 16 → 28 hours, +12 overall/mix, zero within contribution |
| Slower handling | 16 → 20 hours, +4 overall/within, zero mix contribution |
| Stable operation | 16 → 16 hours; zero change and contributions |
| South slowdown with segment question | Complete report, 16 → 18.67 hours, +2.67 overall; three rendered comparison charts |
| Constructed workload association | Complete report, Pearson r = 1; 84 February region-day pairs; noncausal limitations visible |
| Missing category | Partial report; +16 comparison retained; attribution withheld with classification caveats |
| Missing staffing | Partial report; comparison/decomposition retained; workload blocked with DATA_QUALITY_BLOCKED; 2 calls, 3 reserved steps, 0 model calls |
| Exact duplicates | Complete +16 report; 91 → 90 request records and 271 → 270 operations records; one duplicate per table disclosed |
| Invalid timestamp | Unavailable report; numerical metrics/findings withheld; prior report values cleared |
| January → March | Complete 16 → 32 hour result, +16 overall, +12 mix, +4 within |
| February → March, simple comparison | Complete 32 → 32 hour result, zero change; selected periods retained |
| JSON export | Downloaded and parsed; complete one-tool report; zero change and correct February/March scope; no raw-record/credential/prompt fields |
| SVG export | Downloaded and parsed; matching zero-change chart, units, periods, samples, and limitations; no executable SVG elements |

The browser's download-event observer timed out for iframe downloads, but both files were delivered to the shared download directory and independently parsed. This was an observation-tool limitation; no duplicate download was required.

## Live-discovered presentation correction

The fixture description described the January → February scenario construction even when February → March was selected. The computed zero-change result and period caption were correct. The UI now explicitly labels the description “Scenario setup (January → February)” so it cannot be mistaken for the computed comparison's conclusion. A Streamlit regression exercises the zero-change alternate period alongside that label and the actual result caption.

The correction shipped in `555b44eef12c82633fe4265926d3db21d31ad0bc`. Its [GitHub workflow](https://github.com/wushuchris/14-data-analysis-agent/actions/runs/42305730693) passed 488 tests, container checks, and deployment. The rebuilt Space returned to Running. The corrected setup label was checked with February → March: 32 → 32 hours, zero change, and correct scope. A fresh default AI submission then passed model planning and model-selected findings: three completed required tools, two model calls, complete requested work/report, and a verified +16 hour finding. Audit events showed initial_plan requested/received and findings requested/received, without correction, fallback, or quarantine. No further inference was needed.

## Human review

Numerical results matched known answers, calculation scope remained visible, unavailable conclusions stayed withheld, and observations were distinguished from investigation suggestions and causal claims. SQL/Pandas agreement is supplemented by independent known-answer tests. This review does not establish causal validity, statistical significance, model reliability rates, durable billing enforcement, or confidential-data readiness.

## Suggested walkthrough

1. Open the default scenario and explain 16 → 32 hours with +12 mix and +4 within-category contribution.
2. Open Charts to connect the visible numbers to application-owned evidence.
3. Select Missing category and show retained comparison plus withheld attribution.
4. Select Missing staffing and inspect the blocked workload event.
5. Select Invalid timestamp and show that unsupported metrics disappear.
6. Return to the default scenario. Optional AI requires explicit submission and a shared allowance; inspect actual model/tool events rather than inferring AI use from wording.

## Reusable primitive and next use

The reusable primitive is a governed analytical work product: validated operations produce reproducible calculations, evidence-linked facts, charts, and sanitized execution events. A model can choose analytical work and relevant evidence without controlling executable code, numeric truth, or publication authority. It can later support business research or benchmarking pipelines behind approved ingestion and access controls.

## Post-build review

The scoped portfolio build meets its documented analytical and control-boundary goals. Tests, deployment, default/alternate/failure paths, aggregate exports, bounded final-code live inference, and public file/history review are recorded above and in EVALUATION.md/SECURITY.md. The one presentation ambiguity found in this review was fixed in GitHub with a regression test before final-code validation. No Codespace, paid runner, new hosted resource, or billing-setting change was introduced. Profile and private progress records are updated as the closeout step.

The delivered system analyzes completed synthetic service requests; it does not measure unresolved backlog, establish causes, authenticate imported reports, or supply production-grade tenant/billing isolation. Statistical reasoning here is descriptive association and arithmetic decomposition, without significance or causal inference. Those boundaries are deliberate production-upgrade work rather than hidden capabilities.
