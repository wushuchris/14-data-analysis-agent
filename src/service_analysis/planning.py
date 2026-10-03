"""Closed tool schemas and deterministic plans for three supported questions."""
from typing import Annotated, Literal
import json
from pydantic import Field, StrictInt, ValidationError
from service_analysis.analysis import Period, Grouping
from service_analysis.schemas import Contract

MAX_STEPS = 6
MAX_PLAN_BYTES = 32768


class SummaryCall(Contract):
    operation: Literal["summarize"]
    period: Period
    grouping: Grouping = "overall"


class ComparisonCall(Contract):
    operation: Literal["compare"]
    baseline: Period
    comparison: Period
    grouping: Grouping = "overall"


class DecompositionCall(Contract):
    operation: Literal["decompose"]
    baseline: Period
    comparison: Period


class WorkloadCall(Contract):
    operation: Literal["workload"]
    opening_period: Period


ToolCall = Annotated[SummaryCall | ComparisonCall | DecompositionCall | WorkloadCall,
                     Field(discriminator="operation")]


class ProposedStep(Contract):
    call: ToolCall
    depends_on: tuple[StrictInt, ...] = Field(default=(), max_length=MAX_STEPS)


class AnalysisPlan(Contract):
    steps: tuple[ProposedStep, ...] = Field(min_length=1, max_length=MAX_STEPS)


class CompiledStep(Contract):
    step_id: str
    call: ToolCall
    depends_on: tuple[str, ...]


class PlanRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value")


def parse_plan(raw):
    try:
        if isinstance(raw, AnalysisPlan):
            raw = raw.model_dump(mode="json")
        if isinstance(raw, str):
            if len(raw.encode()) > MAX_PLAN_BYTES:
                raise PlanRejected("PLAN_TOO_LARGE")
            raw = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if not isinstance(raw, dict):
            raise ValueError("Expected plan object")
        if len(json.dumps(raw, allow_nan=False).encode()) > MAX_PLAN_BYTES:
            raise PlanRejected("PLAN_TOO_LARGE")
        return AnalysisPlan.model_validate(raw)
    except PlanRejected:
        raise
    except (ValueError, TypeError, RecursionError, ValidationError):
        raise PlanRejected("INVALID_PLAN_SCHEMA") from None


def call_key(call):
    return json.dumps(call.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def compile_plan(raw, coverage, prior=()):
    """Validate the entire round before assigning executable steps.

    Dependencies use zero-based positions in the entire run, including prior
    rounds. The application translates positions into stable step IDs.
    """
    plan = parse_plan(raw)
    offset = len(prior)
    if offset + len(plan.steps) > MAX_STEPS:
        raise PlanRejected("STEP_BUDGET_EXCEEDED")
    seen = {call_key(step.call) for step in prior}
    compiled = []
    for index, proposed in enumerate(plan.steps, offset):
        dependencies = proposed.depends_on
        if len(set(dependencies)) != len(dependencies) or any(d < 0 or d >= index for d in dependencies):
            raise PlanRejected("INVALID_DEPENDENCY")
        call = proposed.call
        key = call_key(call)
        if key in seen:
            raise PlanRejected("DUPLICATE_CALL")
        seen.add(key)
        if isinstance(call, WorkloadCall):
            periods = (call.opening_period,)
            lower, upper = coverage.operations_start, coverage.operations_end
        else:
            periods = (call.period,) if isinstance(call, SummaryCall) else (call.baseline, call.comparison)
            lower, upper = coverage.resolution_start, coverage.resolution_end
        if any(p.start < lower or p.end > upper for p in periods):
            raise PlanRejected("PERIOD_OUTSIDE_COVERAGE")
        if isinstance(call, (ComparisonCall, DecompositionCall)) and call.baseline.end >= call.comparison.start:
            raise PlanRejected("INVALID_COMPARISON_PERIODS")
        compiled.append(CompiledStep(step_id=f"step-{index + 1:03d}", call=call,
                        depends_on=tuple(f"step-{d + 1:03d}" for d in dependencies)))
    return tuple(compiled)


def deterministic_plan(question, baseline: Period, comparison: Period,
                       opening_period: Period | None = None) -> AnalysisPlan:
    compare = ComparisonCall(operation="compare", baseline=baseline, comparison=comparison)
    steps = [ProposedStep(call=compare)]
    if question == "resolution_change":
        pass
    elif question == "segments":
        steps.extend(ProposedStep(call=ComparisonCall(operation="compare", baseline=baseline,
            comparison=comparison, grouping=group), depends_on=(0,)) for group in ("category", "region"))
    elif question == "drivers":
        if opening_period is None:
            raise ValueError("Drivers question requires an explicit opening period")
        steps.extend([
            ProposedStep(call=DecompositionCall(operation="decompose", baseline=baseline,
                         comparison=comparison), depends_on=(0,)),
            ProposedStep(call=WorkloadCall(operation="workload", opening_period=opening_period)),
        ])
    else:
        raise ValueError("Unsupported question")
    return AnalysisPlan(steps=tuple(steps))
