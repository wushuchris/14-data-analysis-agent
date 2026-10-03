# Service Operations Data Analysis Agent

Understand changes in service-request resolution times through reproducible analysis and evidence-backed findings.

**Status: typed inputs and deterministic data-quality preparation implemented. Analytical tools, model integration, and public demo are not yet available.**

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

GitHub is the source of truth. CI runs deterministic schema and quality tests on pushes to main and pull requests. Calculation tests and full analytical evaluations will follow with implementation. Deployment is not configured yet.

The planned Hugging Face deployment will depend on passing tests and evaluations and run only for pushes to main. Pull requests will never deploy. Deployment credentials belong in the GitHub secret `HF_DEPLOY_TOKEN`; inference credentials belong in the Space secret `HF_TOKEN`. No credentials are required for this scaffold.

## Run deterministic tests

Python 3.11 or later:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`prepare_dataset()` returns a typed profile and prepared records. Call `require(scope)` before using records for an analysis. Missing classifications have explicit counts and filtered group views. Blocking request errors release no request records; blocking operations errors release no operations records. Source snapshots are retained separately from public profile output.

## License

MIT. See [LICENSE](LICENSE).
