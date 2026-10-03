import json
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
import pytest
from service_analysis.analysis import Period
from service_analysis.schemas import Coverage
from service_analysis.quality import prepare_dataset
from service_analysis.planning import deterministic_plan
from service_analysis.execution import run_analysis
from service_analysis.findings import build_evidence_catalog
from service_analysis.charts import ChartSpec, build_charts, render_chart_svg
from service_analysis.reporting import assemble_report, export_report_json

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")
OPEN = Period(start="2026-01-01", end="2026-01-05")
SVG = {"s": "http://www.w3.org/2000/svg"}


def dataset(change=10, missing=False):
    requests = []
    for month in (1, 2):
        for category, hours in (("Routine", 10), ("Complex", 30)):
            resolved = datetime(2026, month, 15, tzinfo=timezone.utc)
            duration = hours + (change if month == 2 else 0)
            requests.append(dict(request_id=f"synthetic-{month}-{category}",
                opened_at=(resolved-timedelta(hours=duration)).isoformat(),
                resolved_at=resolved.isoformat(), category=None if missing and category == "Routine" else category,
                region="North"))
    return prepare_dataset(requests, [], Coverage(resolution_start=JAN.start, resolution_end=FEB.end,
        operations_start=OPEN.start, operations_end=OPEN.end, regions=("North",)))


def run(question="segments", **kwargs):
    return run_analysis(dataset(**kwargs), deterministic_plan(question, JAN, FEB, OPEN))


def test_complete_report_and_summary_provenance():
    execution = run()
    report = assemble_report(execution)
    assert report.status == "complete"
    assert report.run_id == execution.run_id and report.dataset_id == execution.dataset_id
    accepted = {f.finding_id: f for f in report.findings.accepted}
    assert len(report.management_summary) <= 5
    for item in report.management_summary:
        assert item.finding_id in accepted
    assert "10 hours" in report.management_summary[0].text
    assert "2026-01-01" in report.management_summary[0].text
    assert "baseline; comparison" in report.management_summary[0].text
    assert report.events == execution.events


def test_chart_finding_values_share_catalog():
    execution = run()
    catalog = {f.evidence_id: f for f in build_evidence_catalog(execution)}
    report = assemble_report(execution)
    assert len(report.charts) == 3
    for chart in report.charts:
        for point in chart.points:
            assert point.evidence == catalog[point.evidence.evidence_id]
    for finding in report.findings.accepted:
        assert finding.evidence == catalog[finding.evidence.evidence_id]


def test_blocked_step_keeps_report_partial():
    report = assemble_report(run("drivers"))
    assert report.status == "partial" and report.execution_status == "partial"
    assert report.findings.status == "completed"
    assert "DATA_QUALITY_BLOCKED" in report.notices
    assert "EXECUTION_INCOMPLETE" in report.notices
    assert report.steps[-1].status == "blocked"
    assert len(report.charts) == 2


def test_quarantine_keeps_charts_and_valid_findings():
    execution = run()
    fact = next(f for f in build_evidence_catalog(execution) if f.reference.metric == "mean_change_hours")
    good = {"reference": fact.reference.model_dump(mode="json"), "claimed_value": 10}
    report = assemble_report(execution, {"findings": [good, {**good, "claimed_value": 999}]})
    assert report.status == "partial" and len(report.findings.accepted) == 1
    assert report.findings.quarantined[0].code == "VALUE_MISMATCH"
    assert "FINDINGS_QUARANTINED" in report.notices
    assert report.charts[0].points[0].evidence.value == 10


@pytest.mark.parametrize("drafts", [{}, {"findings": []}, {"findings": [{"observation": "invented claim"}]}])
def test_explicit_bad_or_empty_drafts_not_silently_replaced(drafts):
    report = assemble_report(run(), drafts)
    assert not report.findings.accepted and not report.management_summary
    assert report.status == "partial" and report.charts
    assert "NO_VERIFIED_FINDINGS" in report.notices
    assert "invented claim" not in export_report_json(report)


def test_rejected_run_unavailable_not_stable():
    execution = run_analysis(dataset(), {"steps": []})
    report = assemble_report(execution)
    assert report.status == "unavailable" and report.execution_status == "rejected"
    assert not report.charts and not report.management_summary
    assert "NO_CALCULATION_EVIDENCE" in report.notices


def test_partial_classification_caveats_follow_chart():
    report = assemble_report(run(missing=True))
    chart = next(c for c in report.charts if c.points[0].evidence.grouping == "category")
    assert report.status == "partial" and chart.status == "partial"
    assert "MISSING_CLASSIFICATION_EXCLUDED" in chart.warnings
    assert chart.points[0].evidence.excluded_classification_counts == (1, 1)
    assert chart.points[0].evidence.population_sizes == (2, 2)


def test_decomposition_chart_known_values():
    report = assemble_report(run("drivers"))
    chart = next(c for c in report.charts if c.kind == "decomposition")
    values = {p.label: p.evidence.value for p in chart.points}
    assert values == pytest.approx({"Case mix": 0, "Within category": 10, "Observed total": 10})
    assert "ARITHMETIC_ATTRIBUTION_NOT_CAUSATION" in chart.warnings
    assert "MEAN_ONLY" in chart.warnings


def test_incomplete_decomposition_nulls_have_no_bars():
    chart = next(c for c in assemble_report(run("drivers", missing=True)).charts if c.kind == "decomposition")
    assert chart.status == "partial"
    assert sum(p.evidence.value is None for p in chart.points) == 2
    svg = ET.fromstring(render_chart_svg(chart))
    assert len(svg.findall('.//s:rect[@class="data-bar"]', SVG)) == 1
    assert "".join(svg.itertext()).count("Unavailable") == 2


@pytest.mark.parametrize("change,domain", [(10, (0, 10)), (-5, (-5, 0)), (0, (0, 1))])
def test_bar_domain_includes_zero_and_signed_change(change, domain):
    chart = assemble_report(run(change=change)).charts[0]
    assert chart.domain == domain
    svg = ET.fromstring(render_chart_svg(chart))
    bar = svg.find('.//s:rect[@class="data-bar"]', SVG)
    zero = svg.find(".//s:line", SVG)
    assert float(bar.attrib["width"]) >= 0
    if change < 0:
        assert float(bar.attrib["x"]) < float(zero.attrib["x1"])
    elif change > 0:
        assert float(bar.attrib["x"]) == float(zero.attrib["x1"])
    else:
        assert float(bar.attrib["width"]) == 0


def workload_run(constant=False):
    requests, ops = [], []
    for i in range(5):
        opened = datetime(2026, 1, i+1, tzinfo=timezone.utc)
        hours = 2 if constant else 2*(i+1)
        requests.append(dict(request_id=f"work-{i}", opened_at=opened.isoformat(),
            resolved_at=(opened+timedelta(hours=hours)).isoformat(), category="Routine", region="North"))
        ops.append(dict(date=opened.date().isoformat(), region="North", incoming_requests=i+1, staffed_hours=1))
    data = prepare_dataset(requests, ops, Coverage(resolution_start=JAN.start, resolution_end=JAN.end,
        operations_start=OPEN.start, operations_end=OPEN.end, regions=("North",)))
    return run_analysis(data, {"steps": [{"call": {"operation": "workload",
        "opening_period": OPEN.model_dump(mode="json")}}]})


def test_correlation_scale_and_selection():
    chart = assemble_report(workload_run()).charts[0]
    assert chart.domain == (-1, 1) and chart.kind == "correlation"
    assert [p.evidence.value for p in chart.points] == pytest.approx([1, 1])
    assert chart.points[0].evidence.selection == "opening_date"
    assert chart.points[0].evidence.sample_unit == "region_days"
    assert "NO_SIGNIFICANCE_TEST" in chart.warnings
    assert "ASSOCIATION_NOT_CAUSATION" in chart.warnings


def test_unavailable_correlation_is_not_zero_bar():
    report = assemble_report(workload_run(constant=True))
    chart = report.charts[0]
    assert report.status == "partial" and chart.status == "unavailable"
    svg = ET.fromstring(render_chart_svg(chart))
    assert not svg.findall('.//s:rect[@class="data-bar"]', SVG)


@pytest.mark.parametrize("field,value", [
    ("units", "percent"), ("dataset_id", "wrong"),
    ("periods", (FEB,)), ("selection", "opening_date"), ("value", float("nan")),
])
def test_chart_scope_or_nonfinite_tamper_rejected(field, value):
    chart = assemble_report(run()).charts[1]
    point = chart.points[0]
    changed = point.model_copy(update={"evidence": point.evidence.model_copy(update={field: value})})
    chart = chart.model_copy(update={"points": (changed,)+chart.points[1:]})
    with pytest.raises(ValueError):
        render_chart_svg(chart)


def test_svg_escaping_and_accessible_description():
    chart = assemble_report(run()).charts[0].model_copy(update={"title": "<script>alert('x')</script>"})
    output = render_chart_svg(chart)
    assert "<script>" not in output and "&lt;script&gt;" in output
    svg = ET.fromstring(output)
    assert svg.attrib["role"] == "img" and svg.find("s:title", SVG) is not None
    assert "resolution date" in svg.find("s:desc", SVG).text


def test_export_aggregate_only_and_roundtrip():
    execution = run()
    report = assemble_report(execution)
    output = export_report_json(report)
    parsed = json.loads(output)
    assert parsed["report_id"] == report.report_id
    assert parsed["events"][-1]["kind"] == "run_finished"
    assert "synthetic-1-Routine" not in output and "opened_at" not in output
    assert "request_id" not in output and "original_snapshot" not in output
    assert "NaN" not in output and "Infinity" not in output


def test_report_identity_same_run_stable_different_run_distinct():
    execution = run()
    first, second = assemble_report(execution), assemble_report(execution)
    assert first.report_id == second.report_id
    another = assemble_report(run())
    assert another.report_id != first.report_id
    assert [c.chart_id for c in first.charts] == [c.chart_id for c in another.charts]


def test_summary_tool_chart():
    execution = run_analysis(dataset(), {"steps": [{"call": {"operation": "summarize",
        "period": JAN.model_dump(mode="json")}}]})
    chart = assemble_report(execution).charts[0]
    assert chart.kind == "mean_resolution"
    assert chart.points[0].evidence.value == 20
    assert chart.points[0].evidence.periods == (JAN,)
