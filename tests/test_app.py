import pytest
from streamlit.testing.v1 import AppTest


def app():
    return AppTest.from_file("app.py", default_timeout=30).run()


def submit(at, scenario=None, question=None, comparison=None):
    if scenario is not None:
        at.selectbox(key="scenario").select(scenario)
    if question is not None:
        at.selectbox(key="question").select(question)
    if comparison is not None:
        at.selectbox(key="comparison").select(comparison)
    return at.button(key="run_analysis").click().run()


def test_default_demo_business_result_and_no_model_calls():
    at = app()
    assert not at.exception
    assert [m.value for m in at.metric] == ["16.00 hours", "32.00 hours", "+16.00 hours"]
    assert at.session_state["_demo_result"].model_calls == 0
    assert at.success
    assert [t.label for t in at.tabs] == ["Findings", "Charts", "Data quality", "Execution evidence"]


def test_scenario_change_replaces_result_and_preserves_oracle():
    at = app()
    first = at.session_state["_demo_result"].execution.run_id
    submit(at, scenario="case_mix")
    assert not at.exception
    assert [m.value for m in at.metric] == ["16.00 hours", "28.00 hours", "+12.00 hours"]
    result = at.session_state["_demo_result"]
    assert result.execution.run_id != first
    assert result.report.status == "complete"
    assert at.session_state["_demo_selection"][0] == "case_mix"


def test_rerender_does_not_execute_again():
    at = app()
    original = at.session_state["_demo_result"].execution.run_id
    at.run()
    assert not at.exception
    assert at.session_state["_demo_result"].execution.run_id == original


def test_missing_category_keeps_valid_results_and_partial_notice():
    at = submit(app(), scenario="missing_category")
    assert not at.exception
    assert at.session_state["_demo_result"].report.status == "partial"
    assert any("Partial analysis" in w.value for w in at.warning)
    assert at.metric[2].value == "+16.00 hours"
    assert any("Missing classifications prevent complete attribution" in m.value for m in at.markdown)


def test_missing_staffing_does_not_suppress_resolution_comparison():
    at = submit(app(), scenario="missing_staffing")
    assert not at.exception
    result = at.session_state["_demo_result"]
    assert result.report.status == "partial"
    assert result.execution.outcomes[-1].status == "blocked"
    assert at.metric[2].value == "+16.00 hours"


def test_invalid_records_withhold_metrics_and_charts():
    at = submit(app(), scenario="invalid_records")
    assert not at.exception
    assert at.session_state["_demo_result"].report.status == "unavailable"
    assert not at.metric
    assert any("Analysis unavailable" in e.value for e in at.error)
    assert not at.session_state["_demo_result"].report.charts


def test_alternate_question_uses_two_group_breakdowns():
    at = submit(app(), question="segments")
    assert not at.exception
    result = at.session_state["_demo_result"]
    assert result.execution.tool_calls == 3
    assert all(s.call.operation == "compare" for s in result.execution.outcomes)
    assert result.report.status == "complete"


def test_alternate_period_changes_caption_and_calculation_scope():
    at = submit(app(), comparison="February → March")
    assert not at.exception
    assert at.metric[2].value == "+0.00 hours"
    assert any("February → March" in c.value for c in at.caption)
    result = at.session_state["_demo_result"]
    assert result.execution.outcomes[0].call.baseline.start.isoformat() == "2026-02-01"


def test_workload_scenario_and_region_day_semantics():
    at = submit(app(), scenario="workload")
    assert not at.exception
    result = at.session_state["_demo_result"]
    chart = next(c for c in result.report.charts if c.kind == "correlation")
    assert chart.points[0].evidence.value == pytest.approx(1, abs=1e-9)
    assert chart.points[0].evidence.sample_unit == "region_days"


def test_no_upload_secret_entry_or_live_model_controls():
    at = app()
    assert not at.exception
    assert not at.get("file_uploader") and not at.text_input and not at.text_area
    assert len(at.selectbox) == 3
