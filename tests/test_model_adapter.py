import json
from datetime import datetime, timedelta, timezone
import pytest
from service_analysis.analysis import Period
from service_analysis.schemas import Coverage
from service_analysis.quality import prepare_dataset
from service_analysis.planning import deterministic_plan
from service_analysis.model_adapter import (
    AnalysisRequest, AdapterPolicy, ModelResponse, run_assisted_analysis,
)

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")


def dataset():
    rows = []
    for month, hours in ((1, 10), (2, 20)):
        resolved = datetime(2026, month, 15, tzinfo=timezone.utc)
        rows.append(dict(request_id=f"synthetic-source-marker-{month}",
            opened_at=(resolved-timedelta(hours=hours)).isoformat(), resolved_at=resolved.isoformat(),
            category="Routine", region="North"))
    return prepare_dataset(rows, [], Coverage(resolution_start=JAN.start, resolution_end=FEB.end,
        operations_start=JAN.start, operations_end=JAN.end, regions=("North",)))


def request(question="resolution_change"):
    return AnalysisRequest(question=question, baseline=JAN, comparison=FEB)


def plan(question="resolution_change"):
    return deterministic_plan(question, JAN, FEB).model_dump(mode="json")


def response(value, finish="stop"):
    return ModelResponse(text=value if isinstance(value, str) else json.dumps(value), finish_reason=finish)


def findings(model_request):
    payload = json.loads(model_request.payload_json)
    evidence = next(f for f in payload["evidence"] if f["reference"]["metric"] == "mean_change_hours")
    return response({"findings": [{"reference": evidence["reference"], "claimed_value": evidence["value"]}]})


class ScriptedTransport:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []

    def complete(self, model_request):
        self.requests.append(model_request)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value(model_request) if callable(value) else value


def test_model_success_preserves_validation_and_actual_events():
    transport = ScriptedTransport(response(plan()), response({"plan": None}), findings)
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.planning_source == result.findings_source == "model"
    assert result.report.status == "complete" and result.requested_work_status == "complete"
    assert result.report.findings.accepted[0].evidence.value == 10
    assert result.model_calls == 3
    assert [r.purpose for r in transport.requests] == ["initial_plan", "followup", "findings"]
    assert [e.sequence for e in result.model_events] == list(range(1, len(result.model_events)+1))
    assert result.execution.tool_calls == 1


def test_transport_omitted_is_deterministic_and_cost_free():
    result = run_assisted_analysis(dataset(), request())
    assert result.model_calls == 0 and not result.model_events
    assert result.planning_source == result.findings_source == "deterministic"
    assert result.report.status == "complete"


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("synthetic-sensitive-diagnostic"), "MODEL_TIMEOUT"),
    (RuntimeError("synthetic-sensitive-diagnostic"), "MODEL_TRANSPORT_FAILED"),
    (response("{}", "length"), "MODEL_RESPONSE_TRUNCATED"),
    (response("{}", "refusal"), "MODEL_REFUSED"),
    (response("{}", "error"), "MODEL_RESPONSE_FAILED"),
    ({"text": "{}"}, "INVALID_MODEL_RESPONSE"),
    (response("x"*33000), "MODEL_RESPONSE_TOO_LARGE"),
])
def test_initial_operational_failure_falls_back_once(failure, code):
    transport = ScriptedTransport(failure)
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.model_calls == 1 and result.execution.tool_calls == 1
    assert result.planning_source == result.findings_source == "deterministic"
    assert code in result.fallback_codes
    assert "synthetic-sensitive-diagnostic" not in result.model_dump_json()


def test_one_plan_correction():
    transport = ScriptedTransport(response({"steps": []}), response(plan()), findings)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False)
    assert result.execution.correction_used and result.execution.tool_calls == 1
    assert result.report.status == "complete"
    assert [r.purpose for r in transport.requests] == ["initial_plan", "correction", "findings"]


@pytest.mark.parametrize("bad", [
    {"steps": []}, {"steps": [{"call": {"operation": "execute_python", "code": "pass"}}]},
    '{"steps":[],"steps":[]}', "not json", '{"steps":NaN}',
])
def test_twice_rejected_planning_retains_attempt_audit(bad):
    transport = ScriptedTransport(response(bad), response(bad))
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.model_calls == 2 and result.execution.tool_calls == 1
    assert result.attempted_execution.tool_calls == 0
    assert result.planning_source == "deterministic"
    assert "MODEL_PLAN_REJECTED" in result.fallback_codes


def test_failed_correction_fallback_before_dispatch():
    transport = ScriptedTransport(response({}), TimeoutError())
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.planning_source == "deterministic"
    assert result.attempted_execution.status == "failed"
    assert result.attempted_execution.tool_calls == 0


def test_requested_periods_cannot_drift_inside_coverage():
    wrong = plan()
    wrong["steps"][0]["call"]["baseline"] = {"start": "2026-01-02", "end": "2026-01-31"}
    transport = ScriptedTransport(response(wrong), response(plan()), findings)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False)
    assert result.execution.correction_used
    assert "REQUEST_SCOPE_MISMATCH" in [e.code for e in result.model_events]
    assert result.report.findings.accepted[0].evidence.periods == (JAN, FEB)


def test_missing_overall_comparison_requires_correction():
    only_summary = {"steps": [{"call": {"operation": "summarize", "period": JAN.model_dump(mode="json")}}]}
    transport = ScriptedTransport(response(only_summary), response(plan()), findings)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False)
    assert "MISSING_OVERALL_COMPARISON" in [e.code for e in result.model_events]
    assert result.execution.tool_calls == 1


def test_followup_receives_computed_evidence_and_global_dependencies():
    def followup(model_request):
        payload = json.loads(model_request.payload_json)
        assert payload["remaining_steps"] == 5
        assert payload["prior_steps"][0]["step_id"] == "step-001"
        assert any(f["value"] == 10 for f in payload["evidence"])
        return response({"plan": {"steps": [{"call": {"operation": "summarize",
            "period": FEB.model_dump(mode="json")}, "depends_on": [0]}]}})
    transport = ScriptedTransport(response(plan()), followup, findings)
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.execution.tool_calls == 2
    assert result.execution.outcomes[1].depends_on == ("step-001",)


def test_followup_failure_preserves_work_without_replay():
    transport = ScriptedTransport(response(plan()), TimeoutError("synthetic-sensitive-diagnostic"), findings)
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.execution.tool_calls == 1 and result.execution.status == "partial"
    assert result.attempted_execution is None
    assert result.report.findings.accepted and result.report.status == "partial"
    assert "synthetic-sensitive-diagnostic" not in result.model_dump_json()


def test_correction_is_shared_across_rounds():
    transport = ScriptedTransport(response({}), response(plan()), response({"plan": {"steps": []}}), findings)
    result = run_assisted_analysis(dataset(), request(), transport)
    assert result.model_calls == 4 and result.execution.tool_calls == 1
    assert [r.purpose for r in transport.requests].count("correction") == 1
    assert result.execution.status == "partial"


@pytest.mark.parametrize("bad", [
    response("truncated", "length"), TimeoutError(), response({"findings": []}),
    response({"findings": [{"observation": "unsupported synthetic claim"}]}), response("malformed"),
])
def test_findings_failure_uses_factual_fallback(bad):
    transport = ScriptedTransport(response(plan()), bad)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False)
    assert result.execution.tool_calls == 1
    assert result.findings_source == "deterministic" and result.report.findings.accepted
    assert result.fallback_codes
    assert "unsupported synthetic claim" not in result.model_dump_json()


def test_partially_valid_findings_keep_quarantine():
    def mixed(model_request):
        good = json.loads(findings(model_request).text)["findings"][0]
        return response({"findings": [good, {**good, "claimed_value": 999}]})
    transport = ScriptedTransport(response(plan()), mixed)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False)
    assert result.findings_source == "model" and result.report.status == "partial"
    assert len(result.report.findings.accepted) == 1 and result.report.findings.quarantined


def test_model_call_budget_separate_from_tool_budget():
    transport = ScriptedTransport(response(plan()))
    result = run_assisted_analysis(dataset(), request(), transport, policy=AdapterPolicy(maximum_calls=1))
    assert result.model_calls == len(transport.requests) == 1
    assert result.execution.tool_calls == 1
    assert "MODEL_CALL_BUDGET_EXHAUSTED" in result.fallback_codes
    assert result.findings_source == "deterministic"


def test_input_budget_rejects_before_transport():
    transport = ScriptedTransport()
    result = run_assisted_analysis(dataset(), request(), transport, policy=AdapterPolicy(maximum_input_bytes=1024))
    assert not transport.requests and result.model_calls == 0
    assert "MODEL_INPUT_TOO_LARGE" in result.fallback_codes


def test_context_excludes_records_prompts_and_diagnostics():
    transport = ScriptedTransport(response(plan()), response({"plan": None}), findings)
    result = run_assisted_analysis(dataset(), request(), transport)
    for model_request in transport.requests:
        payload = json.loads(model_request.payload_json)
        assert "synthetic-source-marker" not in model_request.payload_json
        assert "opened_at" not in model_request.payload_json
        assert "_original" not in model_request.payload_json and "row_numbers" not in model_request.payload_json
        schema = json.loads(model_request.response_schema_json)
        assert schema["additionalProperties"] is False
        assert model_request.timeout_seconds == 20 and model_request.maximum_output_tokens == 2000
        assert payload["request"]["question"] == "resolution_change"
    assert "payload_json" not in result.model_dump_json()
    assert "response_schema_json" not in result.model_dump_json()


def test_evidence_cap_disclosed():
    def limited_findings(model_request):
        payload = json.loads(model_request.payload_json)
        assert len(payload["evidence"]) == 1
        assert payload["evidence_truncated"] and payload["evidence_total"] > 1
        return response({"findings": []})
    transport = ScriptedTransport(response(plan()), limited_findings)
    result = run_assisted_analysis(dataset(), request(), transport, allow_followup=False,
                                   policy=AdapterPolicy(maximum_evidence_facts=1))
    assert result.findings_source == "deterministic"


def test_omitted_required_segments_cannot_claim_complete():
    transport = ScriptedTransport(response(plan()), findings)
    result = run_assisted_analysis(dataset(), request("segments"), transport, allow_followup=False)
    assert result.execution.status == "completed"
    assert result.requested_work_status == "partial" and result.report.status == "partial"
    assert "REQUESTED_ANALYSES_INCOMPLETE" in result.report.notices


def test_invalid_application_request_never_calls_transport():
    transport = ScriptedTransport()
    outside = AnalysisRequest(question="resolution_change", baseline=JAN,
        comparison=Period(start="2027-01-01", end="2027-01-31"))
    with pytest.raises(ValueError):
        run_assisted_analysis(dataset(), outside, transport)
    assert not transport.requests
    with pytest.raises(ValueError):
        run_assisted_analysis(dataset(), request("drivers"), transport)


def test_sessions_have_separate_budgets_and_event_ids():
    first = ScriptedTransport(response(plan()), findings)
    second = ScriptedTransport(response(plan()), findings)
    a = run_assisted_analysis(dataset(), request(), first, allow_followup=False)
    b = run_assisted_analysis(dataset(), request(), second, allow_followup=False)
    assert a.model_calls == b.model_calls == 2
    assert a.execution.run_id != b.execution.run_id
    assert a.model_events[0].request_id != b.model_events[0].request_id
