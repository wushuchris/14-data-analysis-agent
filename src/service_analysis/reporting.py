"""Assemble a reproducible report from an application-owned execution run."""
from typing import Literal
from service_analysis.schemas import Contract
from service_analysis.analysis import _identity
from service_analysis.execution import RunReport, RunEvent
from service_analysis.findings import (
    FindingsReport, build_evidence_catalog, verify_findings, deterministic_findings,
)
from service_analysis.charts import ChartSpec, build_charts


class SummaryItem(Contract):
    finding_id: str
    text: str


class StepSummary(Contract):
    step_id: str
    operation: str
    status: str
    code: str | None
    result_id: str | None


class AnalysisReport(Contract):
    report_id: str
    run_id: str
    dataset_id: str
    status: Literal["complete", "partial", "unavailable"]
    execution_status: str
    findings: FindingsReport
    management_summary: tuple[SummaryItem, ...]
    charts: tuple[ChartSpec, ...]
    notices: tuple[str, ...]
    steps: tuple[StepSummary, ...]
    events: tuple[RunEvent, ...]
    tool_calls: int
    reserved_steps: int
    method: Literal["analysis-report-v1"] = "analysis-report-v1"


def _summary(finding):
    fact = finding.evidence
    labels = {
        "mean_hours": "Mean resolution time", "median_hours": "Median resolution time",
        "mean_change_hours": "Change in mean resolution time",
        "median_change_hours": "Change in median resolution time",
        "observed_mean_change_hours": "Observed change in mean resolution time",
        "mix_contribution_hours": "Arithmetic case-mix contribution",
        "within_category_contribution_hours": "Arithmetic within-category contribution",
        "pearson_r": "Descriptive workload correlation",
    }
    label = labels.get(fact.reference.metric, fact.reference.metric.replace("_", " ").capitalize())
    units = {"elapsed_calendar_hours": "hours", "percent": "%", "completed_requests": "completed requests",
             "region_days": "region-days", "pearson_coefficient": "Pearson r"}[fact.units]
    periods = "; ".join(f"{p.start} to {p.end}" for p in fact.periods)
    text = (f"{fact.reference.group}: {label} = {format(fact.value, '.12g')} {units}. "
            f"{fact.selection.replace('_', ' ').capitalize()}: {periods}"
            + (" (baseline; comparison)." if len(fact.periods) == 2 else "."))
    return SummaryItem(finding_id=finding.finding_id, text=text)


def assemble_report(run: RunReport, finding_drafts=None) -> AnalysisReport:
    """Accept raw proposals, never a caller-supplied 'verified' finding report.

    Omitted drafts use the deterministic factual fallback. Explicit invalid or
    empty drafts retain their rejection/empty status; they are not silently
    replaced. Charts independently project validated calculation evidence.
    """
    catalog = build_evidence_catalog(run)
    findings = (deterministic_findings(run) if finding_drafts is None
                else verify_findings(run, finding_drafts))
    charts = build_charts(catalog)
    notices = ["COMPLETED_REQUESTS_ONLY"]
    notices.extend(warning for fact in catalog for warning in fact.warnings)
    notices.extend(step.code for step in run.outcomes if step.code)
    notices.extend(event.code for event in run.events
                   if event.code and event.kind in ("plan_rejected", "planner_failed", "budget_exhausted"))
    if run.status != "completed":
        notices.append("EXECUTION_INCOMPLETE")
    if findings.quarantined:
        notices.append("FINDINGS_QUARANTINED")
    if findings.code:
        notices.append(findings.code)
    if not findings.accepted:
        notices.append("NO_VERIFIED_FINDINGS")
    if not catalog:
        notices.append("NO_CALCULATION_EVIDENCE")
    if not any(fact.value is not None for fact in catalog):
        status = "unavailable"
    elif (run.status == "completed" and findings.status == "completed"
          and all(chart.status == "complete" for chart in charts)):
        status = "complete"
    else:
        status = "partial"
    steps = tuple(StepSummary(step_id=s.step_id, operation=s.call.operation, status=s.status,
                              code=s.code, result_id=s.result.result_id if s.result else None)
                  for s in run.outcomes)
    payload = {
        "run_id": run.run_id, "dataset_id": run.dataset_id, "status": status,
        "execution_status": run.status, "findings": findings,
        "management_summary": tuple(_summary(f) for f in findings.accepted[:5]),
        "charts": charts, "notices": tuple(dict.fromkeys(notices)), "steps": steps,
        "events": run.events, "tool_calls": run.tool_calls, "reserved_steps": run.reserved_steps,
    }
    report = AnalysisReport(report_id="pending", **payload)
    identity = _identity("report-", report.model_dump(mode="json", exclude={"report_id"}))
    return report.model_copy(update={"report_id": identity})


def export_report_json(report: AnalysisReport) -> str:
    """Structured export contains aggregate evidence, never prepared raw records."""
    validated = AnalysisReport.model_validate(report.model_dump(mode="json"))
    return validated.model_dump_json(indent=2)
