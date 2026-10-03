import json
from datetime import datetime, timedelta, timezone
import pytest
from service_analysis.analysis import Period, SummaryResult, summarize_resolution, CalculationMismatch
from service_analysis.schemas import Coverage
from service_analysis.quality import prepare_dataset
from service_analysis.planning import AnalysisPlan, deterministic_plan
from service_analysis.execution import run_analysis, run_analysis_iter, RunReport
from service_analysis import execution

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")
MAR = Period(start="2026-03-01", end="2026-03-31")
OPEN = Period(start="2026-01-01", end="2026-01-01")


def source(missing_category=False, invalid=False):
    rows = []
    for month, hours in [(1, 10), (2, 20)]:
        resolved = datetime(2026, month, 15, tzinfo=timezone.utc)
        rows.append(dict(request_id=str(month), opened_at=(resolved-timedelta(hours=hours)).isoformat(),
                         resolved_at=resolved.isoformat(), category=None if missing_category else "Routine",
                         region="North"))
    if invalid:
        rows[0]["resolved_at"] = "invalid"
    return prepare_dataset(rows, [], Coverage(resolution_start=JAN.start, resolution_end=MAR.end,
                           operations_start=OPEN.start, operations_end=OPEN.end, regions=("North",)))


def summary(period=JAN, grouping="overall", deps=()):
    return dict(call=dict(operation="summarize", period=period.model_dump(mode="json"), grouping=grouping),
                depends_on=list(deps))


def proposal(*steps):
    return dict(steps=list(steps))


def six_steps():
    return [summary(p, g) for p in (JAN, FEB) for g in ("overall", "category", "region")]


def test_real_segments():
    report = run_analysis(source(), deterministic_plan("segments", JAN, FEB))
    assert report.status == "completed" and report.tool_calls == 3
    assert [s.step_id for s in report.outcomes] == ["step-001", "step-002", "step-003"]
    assert report.outcomes[1].depends_on == ("step-001",)
    assert report.outcomes[0].result.rows[0].mean_change_hours == 10


def test_drivers_retains_results_when_workload_blocked():
    report = run_analysis(source(), deterministic_plan("drivers", JAN, FEB, OPEN))
    assert [s.status for s in report.outcomes] == ["completed", "completed", "blocked"]
    assert report.tool_calls == 2 and report.status == "partial"


@pytest.mark.parametrize("bad", [
    {"call": {"operation": "execute_python", "code": "pass"}},
    {"call": {"operation": "summarize", "period": JAN.model_dump(mode="json"), "sql": "SELECT 1"}},
    {"call": {"operation": "workload", "opening_period": OPEN.model_dump(mode="json"), "minimum_pairs": 1}},
    {**summary(FEB), "step_id": "forged"},
    summary(FEB, deps=(True,)),
])
def test_invalid_late_step_prevents_all_execution(bad):
    report = run_analysis(source(), proposal(summary(), bad))
    assert report.status == "rejected" and report.tool_calls == 0


@pytest.mark.parametrize("deps", [(0,), (-1,), (9,), (0, 0)])
def test_invalid_dependencies(deps):
    report = run_analysis(source(), proposal(summary(deps=deps)))
    assert report.status == "rejected" and report.tool_calls == 0


def test_duplicate_calls():
    report = run_analysis(source(), proposal(summary(), summary()))
    assert report.events[1].code == "DUPLICATE_CALL"


def test_comparison_overlap():
    plan = deterministic_plan("resolution_change", JAN, JAN)
    report = run_analysis(source(), plan)
    assert report.events[1].code == "INVALID_COMPARISON_PERIODS"


def test_outside_coverage_rejects_entire_round():
    outside = Period(start="2027-01-01", end="2027-01-02")
    report = run_analysis(source(), proposal(summary(), summary(outside)))
    assert report.tool_calls == 0 and report.events[1].code == "PERIOD_OUTSIDE_COVERAGE"


@pytest.mark.parametrize("raw", [
    '{"steps": [], "steps": []}', '{"steps": NaN}', "[]", "{broken",
    "x" * 33000, {"steps": [], "authority": "admin"},
    chr(96)*3 + "json\n{}\n" + chr(96)*3,
])
def test_malformed_input(raw):
    report = run_analysis(source(), raw)
    assert report.status == "rejected" and report.tool_calls == 0


def test_json_plan():
    assert run_analysis(source(), json.dumps(proposal(summary()))).status == "completed"


def test_correction_receives_safe_context_once():
    seen = []
    def correct(context):
        seen.append(context)
        return proposal(summary())
    report = run_analysis(source(), {}, correction=correct)
    assert report.status == "completed" and report.correction_used
    assert len(seen) == 1 and seen[0].remaining_steps == 6
    assert seen[0].rejection_code == "INVALID_PLAN_SCHEMA"


def test_bad_correction_not_retried():
    seen = []
    report = run_analysis(source(), {}, correction=lambda c: seen.append(c) or {})
    assert report.status == "rejected" and len(seen) == 1


def test_correction_budget_shared_across_rounds():
    seen = []
    report = run_analysis(source(), {}, correction=lambda c: seen.append(c) or proposal(summary()),
                          followup=lambda c: {})
    assert report.status == "partial" and report.tool_calls == 1 and len(seen) == 1


def test_followup_observes_results_and_global_dependencies():
    seen = []
    def followup(context):
        seen.append(context)
        assert context.outcomes[0].result.rows[0].mean_hours == 10
        assert context.remaining_steps == 5
        return proposal(summary(FEB, deps=(0,)))
    report = run_analysis(source(), proposal(summary()), followup=followup)
    assert report.status == "completed" and len(seen) == 1
    assert report.accepted_rounds == 2 and report.outcomes[1].depends_on == ("step-001",)


def test_followup_can_decline():
    report = run_analysis(source(), proposal(summary()), followup=lambda c: None)
    assert report.status == "completed"
    assert "followup_declined" in [e.kind for e in report.events]


def test_budget_shared_across_rounds():
    report = run_analysis(source(), proposal(*six_steps()[:5]),
                          followup=lambda c: proposal(six_steps()[5], summary(MAR)))
    assert report.status == "partial" and report.tool_calls == 5
    assert "STEP_BUDGET_EXCEEDED" in [e.code for e in report.events]


def test_exhausted_budget_does_not_call_planner():
    def forbidden(context):
        pytest.fail("Planner must not be called")
    report = run_analysis(source(), proposal(*six_steps()), followup=forbidden)
    assert report.status == "partial" and report.tool_calls == 6
    assert "budget_exhausted" in [e.kind for e in report.events]


def test_seven_initial_steps_rejected():
    report = run_analysis(source(), proposal(*six_steps(), summary(MAR)))
    assert report.status == "rejected" and report.tool_calls == 0


def test_duplicate_across_rounds():
    report = run_analysis(source(), proposal(summary()), followup=lambda c: proposal(summary()))
    assert report.status == "partial" and report.tool_calls == 1


def test_blocked_dependency_does_not_block_independent_step():
    report = run_analysis(source(missing_category=True),
                          proposal(summary(grouping="category"), summary(FEB, deps=(0,)), summary()))
    assert [s.status for s in report.outcomes] == ["blocked", "skipped", "completed"]
    assert report.tool_calls == 1


def test_partial_result_is_retained_but_dependents_skip():
    report = run_analysis(source(), proposal(summary(MAR), summary(deps=(0,))))
    assert [s.status for s in report.outcomes] == ["partial", "skipped"]
    assert report.outcomes[0].result.status == "no_observations"


def test_invalid_dataset_blocks_without_dispatch():
    report = run_analysis(source(invalid=True), proposal(summary()))
    assert report.status == "blocked" and report.tool_calls == 0


@pytest.mark.parametrize("error,code", [
    (CalculationMismatch("private diagnostic"), "CALCULATION_MISMATCH"),
    (RuntimeError("private diagnostic"), "TOOL_EXECUTION_FAILED"),
])
def test_tool_failure_sanitized(monkeypatch, error, code):
    def fail(dataset, call):
        raise error
    monkeypatch.setitem(execution._HANDLERS, "summarize", (fail, SummaryResult))
    report = run_analysis(source(), proposal(summary(), summary(FEB, deps=(0,))))
    assert [s.status for s in report.outcomes] == ["failed", "skipped"]
    assert report.outcomes[0].code == code
    assert "private diagnostic" not in report.model_dump_json()


@pytest.mark.parametrize("fault", ["dataset", "period", "type"])
def test_invalid_output_never_released(monkeypatch, fault):
    def wrong(dataset, call):
        if fault == "type":
            return {"status": "complete"}
        result = summarize_resolution(dataset, FEB if fault == "period" else JAN)
        return result.model_copy(update={"dataset_id": "wrong"}) if fault == "dataset" else result
    monkeypatch.setitem(execution._HANDLERS, "summarize", (wrong, SummaryResult))
    report = run_analysis(source(), proposal(summary()))
    assert report.outcomes[0].code == "TOOL_OUTPUT_INVALID"
    assert report.outcomes[0].result is None


@pytest.mark.parametrize("adapter", ["correction", "followup"])
def test_callback_exception_sanitized(adapter):
    def fail(context):
        raise RuntimeError("private diagnostic")
    report = run_analysis(source(), {} if adapter == "correction" else proposal(summary()), **{adapter: fail})
    assert report.status == ("failed" if adapter == "correction" else "partial")
    assert "private diagnostic" not in report.model_dump_json()


def test_events_stream_real_execution(monkeypatch):
    called = []
    def handler(dataset, call):
        called.append(call)
        return summarize_resolution(dataset, call.period, call.grouping)
    monkeypatch.setitem(execution._HANDLERS, "summarize", (handler, SummaryResult))
    stream = run_analysis_iter(source(), proposal(summary()))
    assert next(stream).kind == "run_started"
    assert next(stream).kind == "plan_accepted"
    assert next(stream).kind == "step_started" and not called
    assert next(stream).kind == "step_completed" and len(called) == 1
    rest = list(stream)
    assert rest[-2].kind == "run_finished"
    report = rest[-1]
    assert isinstance(report, RunReport)
    assert [e.sequence for e in report.events] == list(range(1, len(report.events)+1))


def test_runs_isolated_but_results_stable():
    first = run_analysis(source(), proposal(summary()))
    second = run_analysis(source(), proposal(summary()))
    assert first.run_id != second.run_id
    assert first.outcomes[0].result.result_id == second.outcomes[0].result.result_id


def test_recipe_validation():
    with pytest.raises(ValueError):
        deterministic_plan("unknown", JAN, FEB)
    with pytest.raises(ValueError):
        deterministic_plan("drivers", JAN, FEB)


def test_schema_is_closed_and_discriminated():
    schema = AnalysisPlan.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "discriminator" in json.dumps(schema)
