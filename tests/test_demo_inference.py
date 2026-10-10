"""Synthetic model responses exercise UI-independent production orchestration."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from service_analysis.analysis import Period
from service_analysis.demo_inference import DemoAIController, ReservedTransport, run_demo_analysis
from service_analysis.model_adapter import AdapterError, ModelRequest, ModelResponse
from service_analysis.planning import deterministic_plan


class SyntheticModel:
    def __init__(self, *, wrong_value=False, reject_plan=False, failure=None):
        self.requests = []
        self.wrong_value = wrong_value
        self.reject_plan = reject_plan
        self.failure = failure

    def complete(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        payload = json.loads(request.payload_json)
        if request.purpose in {"initial_plan", "correction"}:
            scope = payload["request"]
            value = {"steps": []} if self.reject_plan else deterministic_plan(
                scope["question"], Period.model_validate(scope["baseline"]),
                Period.model_validate(scope["comparison"]),
                Period.model_validate(scope["opening_period"])).model_dump(mode="json")
        else:
            evidence = next(f for f in payload["evidence"]
                            if f["reference"]["metric"] == "mean_change_hours"
                            and f["reference"]["group"] == "All")
            value = {"findings": [{"reference": evidence["reference"],
                "claimed_value": evidence["value"] + (999 if self.wrong_value else 0)}]}
        return ModelResponse(text=json.dumps(value), finish_reason="stop")


def run(controller, **kwargs):
    return run_demo_analysis("combined", "drivers", "January → February",
                             use_ai=True, controller=controller, **kwargs)


def test_ai_success_is_verified_and_has_no_followup():
    fake = SyntheticModel()
    _, result, notice = run(DemoAIController(factory=lambda: fake))
    assert notice is None
    assert result.planning_source == result.findings_source == "model"
    assert result.report.status == "complete"
    assert result.report.findings.accepted[0].evidence.value == 16
    assert [r.purpose for r in fake.requests] == ["initial_plan", "findings"]
    assert all(r.maximum_output_tokens == 2000 and r.timeout_seconds == 20 for r in fake.requests)


def test_wrong_model_number_is_quarantined_and_falls_back():
    _, result, notice = run(DemoAIController(factory=lambda: SyntheticModel(wrong_value=True)))
    assert notice is None
    assert result.planning_source == "model" and result.findings_source == "deterministic"
    assert "MODEL_FINDINGS_REJECTED" in result.fallback_codes
    assert result.rejected_findings.quarantined[0].code == "VALUE_MISMATCH"
    assert all(f.evidence.value != 1015 for f in result.report.findings.accepted)


def test_plan_correction_still_shares_three_call_budget():
    fake = SyntheticModel(reject_plan=True)
    _, result, _ = run(DemoAIController(factory=lambda: fake))
    assert [r.purpose for r in fake.requests] == ["initial_plan", "correction"]
    assert result.planning_source == result.findings_source == "deterministic"
    assert result.model_calls == 2
    assert result.report.status == "complete"


@pytest.mark.parametrize("failure,code", [(TimeoutError("private"), "MODEL_TIMEOUT"),
    (AdapterError("HF_AUTH_FAILED"), "HF_AUTH_FAILED"),
    (AdapterError("HF_CREDITS_UNAVAILABLE"), "HF_CREDITS_UNAVAILABLE")])
def test_provider_failure_keeps_known_answer(failure, code):
    fake = SyntheticModel(failure=failure)
    _, result, _ = run(DemoAIController(factory=lambda: fake))
    assert result.fallback_codes == (code,)
    assert result.model_calls == 1 and len(fake.requests) == 1
    assert result.report.status == "complete"
    assert result.planning_source == "deterministic"
    assert "private" not in result.model_dump_json()


def test_deterministic_does_not_construct_transport_or_consume_allowance():
    def forbidden():
        raise AssertionError("Transport constructed in deterministic mode")
    controller = DemoAIController(factory=forbidden)
    _, result, notice = run_demo_analysis("combined", "drivers", "January → February",
                                         controller=controller)
    assert result.model_calls == 0 and notice is None
    assert controller._reservations == 0


def test_exhaustion_across_runs_keeps_deterministic_available():
    fake = SyntheticModel()
    controller = DemoAIController(factory=lambda: fake)
    for _ in range(4):
        assert run(controller)[1].model_calls == 2
    _, result, notice = run(controller)
    assert notice == "DEMO_AI_ALLOWANCE_EXHAUSTED"
    assert result.model_calls == 0 and len(fake.requests) == 8
    assert result.report.status == "complete"


def test_concurrent_sessions_can_reserve_only_four_runs():
    controller = DemoAIController(factory=SyntheticModel)
    def reserve(_):
        try:
            controller.reserve_transport()
            return "reserved"
        except AdapterError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(20)))
    assert outcomes.count("reserved") == 4
    assert outcomes.count("DEMO_AI_ALLOWANCE_EXHAUSTED") == 16


def test_no_secret_consumes_no_reservation(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    controller = DemoAIController()
    _, result, notice = run(controller)
    assert notice == "HF_TOKEN_UNAVAILABLE" and result.model_calls == 0
    assert controller._reservations == 0


def test_initialization_error_is_sanitized():
    def unavailable():
        raise ValueError("private credentials or diagnostics")
    _, result, notice = run(DemoAIController(factory=unavailable))
    assert notice == "HF_INITIALIZATION_FAILED" and result.model_calls == 0


def test_reserved_transport_cannot_dispatch_more_than_three_calls():
    class FailingTransport:
        calls = 0
        def complete(self, request):
            self.calls += 1
            raise TimeoutError("private")
    fake = FailingTransport()
    reserved = ReservedTransport(fake)
    request = ModelRequest(request_id="synthetic", purpose="initial_plan", instruction="JSON",
        payload_json="{}", response_schema_json="{}", maximum_output_tokens=2000, timeout_seconds=20)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            reserved.complete(request)
    with pytest.raises(AdapterError, match="DEMO_AI_ALLOWANCE_EXHAUSTED"):
        reserved.complete(request)
    assert fake.calls == 3
    with pytest.raises(AdapterError, match="DEMO_REQUEST_LIMIT_INVALID"):
        ReservedTransport(fake).complete(request.model_copy(update={"maximum_output_tokens":4000}))
