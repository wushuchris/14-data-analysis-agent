"""Finite, shared inference allowance for the public synthetic demonstration."""
from threading import Lock

from service_analysis.analysis import Period
from service_analysis.demo_data import MONTHS, load_scenario
from service_analysis.hf_transport import HFRouterTransport
from service_analysis.model_adapter import (
    AdapterError, AdapterPolicy, AnalysisRequest, run_assisted_analysis,
)

COMPARISONS = {
    "January → February": ("January", "February"),
    "January → March": ("January", "March"),
    "February → March": ("February", "March"),
}
DEMO_POLICY = AdapterPolicy(maximum_calls=3, maximum_output_tokens=2000,
                            timeout_seconds=20)
NOTICE_TEXT = {
    "HF_TOKEN_UNAVAILABLE": "AI is not configured. This report uses deterministic analysis.",
    "DEMO_AI_ALLOWANCE_EXHAUSTED": "The shared AI demo allowance is exhausted. This report uses deterministic analysis.",
    "HF_INITIALIZATION_FAILED": "AI is unavailable. This report uses deterministic analysis.",
}


class ReservedTransport:
    """A reservation can dispatch at most three calls; failures consume a slot."""
    def __init__(self, transport):
        self._transport = transport
        self._remaining = DEMO_POLICY.maximum_calls
        self._lock = Lock()

    def complete(self, request):
        if (request.maximum_output_tokens > DEMO_POLICY.maximum_output_tokens
                or request.timeout_seconds > DEMO_POLICY.timeout_seconds):
            raise AdapterError("DEMO_REQUEST_LIMIT_INVALID")
        with self._lock:
            if self._remaining == 0:
                raise AdapterError("DEMO_AI_ALLOWANCE_EXHAUSTED")
            self._remaining -= 1
        return self._transport.complete(request)


class DemoAIController:
    """Four reservations across all browser sessions in one application process.

    No timer, session reset, or UI action replenishes this allowance. Reserving
    all three possible calls upfront prevents concurrent sessions overspending.
    Unused slots are not refunded. Restart/redeploy resets process memory; this
    is a small-demo safeguard, not durable account-wide spending enforcement.
    """
    def __init__(self, factory=None):
        self._factory = factory
        self._reservations = 0
        self._lock = Lock()

    def reserve_transport(self):
        # Credential/configuration failure consumes no reservation or live call.
        transport = (self._factory or HFRouterTransport)()
        with self._lock:
            if self._reservations >= 4:
                raise AdapterError("DEMO_AI_ALLOWANCE_EXHAUSTED")
            self._reservations += 1
        return ReservedTransport(transport)


# Imported modules persist across Streamlit reruns and are shared by sessions.
CONTROLLER = DemoAIController()


def run_demo_analysis(scenario, question, comparison, *, use_ai=False, controller=None):
    first, second = COMPARISONS[comparison]
    baseline = Period(start=MONTHS[first][0], end=MONTHS[first][1])
    current = Period(start=MONTHS[second][0], end=MONTHS[second][1])
    data = load_scenario(scenario)
    request = AnalysisRequest(question=question, baseline=baseline,
                              comparison=current, opening_period=current)
    transport, notice = None, None
    if use_ai:
        try:
            transport = (controller or CONTROLLER).reserve_transport()
        except AdapterError as error:
            notice = error.code if error.code in NOTICE_TEXT else "HF_INITIALIZATION_FAILED"
        except Exception:
            notice = "HF_INITIALIZATION_FAILED"
    result = run_assisted_analysis(data, request, transport,
                                  policy=DEMO_POLICY, allow_followup=False)
    return data.profile, result, notice
