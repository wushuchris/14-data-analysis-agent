# Service Operations Data Analysis Agent

Understand changes in service-request resolution times through reproducible analysis and evidence-backed findings.

**Status: design and repository scaffold. Analytical implementation and public demo are not yet available.**

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

GitHub is the source of truth. Initial CI will validate the scaffold on pushes to main and pull requests. Analytical tests and evaluations will be added with implementation. Deployment is not configured yet.

The planned Hugging Face deployment will depend on passing tests and evaluations and run only for pushes to main. Pull requests will never deploy. Deployment credentials belong in the GitHub secret `HF_DEPLOY_TOKEN`; inference credentials belong in the Space secret `HF_TOKEN`. No credentials are required for this scaffold.

## License

MIT. See [LICENSE](LICENSE).
