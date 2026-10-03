"""Application-owned bounded execution with real, streamable lifecycle events."""
from typing import Literal
from uuid import uuid4
from pydantic import ValidationError
from service_analysis.schemas import Contract
from service_analysis.analysis import (
    CalculationMismatch, SummaryResult, ComparisonResult, _dataset_id,
    summarize_resolution, compare_periods,
)
from service_analysis.decomposition import DecompositionResult, decompose_case_mix
from service_analysis.workload import WorkloadResult, analyze_workload
from service_analysis.planning import (
    MAX_STEPS, ToolCall, SummaryCall, ComparisonCall, DecompositionCall,
    PlanRejected, compile_plan,
)

ToolResult = SummaryResult | ComparisonResult | DecompositionResult | WorkloadResult
StepStatus = Literal["completed", "partial", "blocked", "failed", "skipped"]


class StepOutcome(Contract):
    step_id: str
    call: ToolCall
    depends_on: tuple[str, ...]
    status: StepStatus
    result: ToolResult | None = None
    code: str | None = None


class RunEvent(Contract):
    run_id: str
    sequence: int
    kind: Literal["run_started", "plan_accepted", "plan_rejected", "correction_requested",
                  "followup_requested", "followup_declined", "planner_failed",
                  "step_started", "step_completed", "step_partial", "step_blocked",
                  "step_failed", "step_skipped", "budget_exhausted", "run_finished"]
    round_number: int | None = None
    step_id: str | None = None
    result_id: str | None = None
    code: str | None = None


class PlanningContext(Contract):
    round_number: int
    remaining_steps: int
    outcomes: tuple[StepOutcome, ...]
    rejection_code: str | None = None


class RunReport(Contract):
    run_id: str
    dataset_id: str
    status: Literal["completed", "partial", "blocked", "failed", "rejected"]
    accepted_rounds: int
    correction_used: bool
    tool_calls: int
    reserved_steps: int
    outcomes: tuple[StepOutcome, ...]
    events: tuple[RunEvent, ...]


# Registry is application-owned. Plans can name only the closed ToolCall union.
def _summary(dataset, call):
    return summarize_resolution(dataset, call.period, call.grouping)


def _comparison(dataset, call):
    return compare_periods(dataset, call.baseline, call.comparison, call.grouping)


def _decomposition(dataset, call):
    return decompose_case_mix(dataset, call.baseline, call.comparison)


def _workload(dataset, call):
    return analyze_workload(dataset, call.opening_period)


_HANDLERS = {
    "summarize": (_summary, SummaryResult),
    "compare": (_comparison, ComparisonResult),
    "decompose": (_decomposition, DecompositionResult),
    "workload": (_workload, WorkloadResult),
}


def _scope(call):
    if isinstance(call, (SummaryCall, ComparisonCall)):
        return "resolution" if call.grouping == "overall" else call.grouping
    return "resolution" if isinstance(call, DecompositionCall) else "workload"


def _validated_result(value, result_type, call, dataset_id):
    if not isinstance(value, result_type):
        raise ValueError("Unexpected result type")
    # Revalidate even model instances; do not trust a constructed/bypassed object.
    result = result_type.model_validate(value.model_dump(mode="json"))
    if result.dataset_id != dataset_id:
        raise ValueError("Result belongs to a different dataset")
    if isinstance(call, SummaryCall):
        valid = result.period == call.period and result.grouping == call.grouping
    elif isinstance(call, (ComparisonCall, DecompositionCall)):
        comparison = result if isinstance(call, ComparisonCall) else result.overall
        valid = comparison.baseline.period == call.baseline and comparison.comparison.period == call.comparison
        if isinstance(call, ComparisonCall):
            valid = valid and comparison.baseline.grouping == call.grouping and comparison.comparison.grouping == call.grouping
    else:
        valid = result.opening_period == call.opening_period
    if not valid:
        raise ValueError("Result does not match requested analysis")
    return result


def run_analysis_iter(dataset, initial_plan, *, correction=None, followup=None):
    """Yield actual RunEvent objects, followed by one final RunReport.

    correction(context) may supply one replacement plan across the entire run.
    followup(context) may supply one additional plan or None. These optional
    adapters are application code, not tool names or executable model output.
    No inference provider is invoked by this module. Exhaust the iterator to
    finish; abandoning it performs no further work and has no resume guarantee.
    """
    run_id = "run-" + uuid4().hex
    dataset_id = _dataset_id(dataset)
    events, outcomes, compiled_history = [], [], []
    tool_calls = accepted_rounds = 0
    correction_used = False
    terminal_issue = None

    def event(kind, **values):
        value = RunEvent(run_id=run_id, sequence=len(events) + 1, kind=kind, **values)
        events.append(value)
        return value

    def context(round_number, rejection=None):
        return PlanningContext(round_number=round_number,
            remaining_steps=MAX_STEPS - len(compiled_history),
            outcomes=tuple(outcomes), rejection_code=rejection)

    yield event("run_started")
    pending = initial_plan
    for round_number in (0, 1):
        if round_number:
            if followup is None:
                break
            if len(compiled_history) == MAX_STEPS:
                terminal_issue = "STEP_BUDGET_EXHAUSTED"
                yield event("budget_exhausted", round_number=round_number, code=terminal_issue)
                break
            yield event("followup_requested", round_number=round_number)
            try:
                pending = followup(context(round_number))
            except Exception:
                terminal_issue = "FOLLOWUP_ADAPTER_FAILED"
                yield event("planner_failed", round_number=round_number, code=terminal_issue)
                break
            if pending is None:
                yield event("followup_declined", round_number=round_number)
                break

        try:
            steps = compile_plan(pending, dataset.coverage, tuple(compiled_history))
        except PlanRejected as rejection:
            yield event("plan_rejected", round_number=round_number, code=rejection.code)
            if correction is None or correction_used:
                terminal_issue = rejection.code
                break
            correction_used = True
            yield event("correction_requested", round_number=round_number, code=rejection.code)
            try:
                replacement = correction(context(round_number, rejection.code))
            except Exception:
                terminal_issue = "CORRECTION_ADAPTER_FAILED"
                yield event("planner_failed", round_number=round_number, code=terminal_issue)
                break
            try:
                steps = compile_plan(replacement, dataset.coverage, tuple(compiled_history))
            except PlanRejected as second:
                terminal_issue = second.code
                yield event("plan_rejected", round_number=round_number, code=second.code)
                break
        compiled_history.extend(steps)
        accepted_rounds += 1
        yield event("plan_accepted", round_number=round_number)
        for step in steps:
            previous = {value.step_id: value for value in outcomes}
            if any(previous[dependency].status != "completed" for dependency in step.depends_on):
                outcome = StepOutcome(**step.model_dump(), status="skipped", code="DEPENDENCY_NOT_COMPLETE")
            else:
                try:
                    dataset.require(_scope(step.call))
                except ValueError:
                    outcome = StepOutcome(**step.model_dump(), status="blocked", code="DATA_QUALITY_BLOCKED")
                else:
                    yield event("step_started", round_number=round_number, step_id=step.step_id)
                    tool_calls += 1
                    handler, result_type = _HANDLERS[step.call.operation]
                    try:
                        value = handler(dataset, step.call)
                    except CalculationMismatch:
                        outcome = StepOutcome(**step.model_dump(), status="failed", code="CALCULATION_MISMATCH")
                    except ValidationError:
                        outcome = StepOutcome(**step.model_dump(), status="failed", code="TOOL_OUTPUT_INVALID")
                    except ValueError:
                        outcome = StepOutcome(**step.model_dump(), status="blocked", code="TOOL_INPUT_REJECTED")
                    except Exception:
                        outcome = StepOutcome(**step.model_dump(), status="failed", code="TOOL_EXECUTION_FAILED")
                    else:
                        try:
                            result = _validated_result(value, result_type, step.call, dataset_id)
                        except (ValueError, TypeError, ValidationError):
                            outcome = StepOutcome(**step.model_dump(), status="failed", code="TOOL_OUTPUT_INVALID")
                        else:
                            status = "completed" if result.status == "complete" else "partial"
                            outcome = StepOutcome(**step.model_dump(), status=status, result=result)
            outcomes.append(outcome)
            yield event("step_" + outcome.status, round_number=round_number,
                        step_id=step.step_id, code=outcome.code,
                        result_id=outcome.result.result_id if outcome.result is not None else None)

    if outcomes and all(value.status == "completed" for value in outcomes) and terminal_issue is None:
        status = "completed"
    elif any(value.result is not None for value in outcomes):
        status = "partial"
    elif any(value.status == "failed" for value in outcomes) or terminal_issue in (
        "FOLLOWUP_ADAPTER_FAILED", "CORRECTION_ADAPTER_FAILED"
    ):
        status = "failed"
    elif outcomes:
        status = "blocked"
    else:
        status = "rejected"
    yield event("run_finished", code=status)
    yield RunReport(run_id=run_id, dataset_id=dataset_id, status=status,
        accepted_rounds=accepted_rounds, correction_used=correction_used,
        tool_calls=tool_calls, reserved_steps=len(compiled_history),
        outcomes=tuple(outcomes), events=tuple(events))


def run_analysis(dataset, initial_plan, *, correction=None, followup=None):
    """Convenience wrapper returning the final report from the same event loop."""
    final = None
    for item in run_analysis_iter(dataset, initial_plan, correction=correction, followup=followup):
        if isinstance(item, RunReport):
            final = item
    return final
