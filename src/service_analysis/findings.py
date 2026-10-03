"""Evidence-backed numerical observations with bounded investigation topics.

Only application-owned RunReport objects establish evidence. These schemas are
not signatures or a substitute for authenticating persisted reports.
"""
import json
from math import isclose, isfinite
from typing import Literal
from pydantic import Field, StrictFloat, StrictInt, field_validator
from service_analysis.schemas import Contract
from service_analysis.analysis import (
    ABS_TOL, REL_TOL, Period, SummaryResult, ComparisonResult, _identity,
)
from service_analysis.decomposition import DecompositionResult
from service_analysis.workload import WorkloadResult
from service_analysis.execution import RunReport, _validated_result
from service_analysis.planning import _pairs, _nonfinite

MAX_FINDINGS = 20
MAX_DRAFT_BYTES = 32768
Metric = Literal[
    "sample_size", "mean_hours", "median_hours", "baseline_count", "comparison_count",
    "count_delta", "mean_change_hours", "median_change_hours",
    "mean_change_percent", "median_change_percent", "observed_mean_change_hours",
    "mix_contribution_hours", "within_category_contribution_hours", "pearson_r", "pair_count",
]
Topic = Literal["none", "category_mix", "category_handling", "regional_process", "staffing_and_demand"]
COUNTS = {"sample_size", "baseline_count", "comparison_count", "count_delta", "pair_count"}
TOPICS = {
    "none": "",
    "category_mix": "Review changes in the types of completed requests.",
    "category_handling": "Investigate differences in handling within service categories.",
    "regional_process": "Compare regional processes and request composition.",
    "staffing_and_demand": "Review staffing and demand alongside request composition and timing.",
}
RESULT_TYPES = {"summarize": SummaryResult, "compare": ComparisonResult,
                "decompose": DecompositionResult, "workload": WorkloadResult}


class EvidenceReference(Contract):
    result_id: str = Field(strict=True, min_length=1, max_length=100)
    metric: Metric
    group: str = Field(default="All", strict=True, min_length=1, max_length=80)


class FindingDraft(Contract):
    reference: EvidenceReference
    claimed_value: StrictInt | StrictFloat
    investigation: Topic = "none"

    @field_validator("claimed_value")
    @classmethod
    def finite(cls, value):
        try:
            finite = isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("Claim must be finite")
        return value


class EvidenceFact(Contract):
    evidence_id: str
    step_id: str
    reference: EvidenceReference
    dataset_id: str
    value: StrictInt | StrictFloat | None
    units: Literal["elapsed_calendar_hours", "percent", "completed_requests", "region_days",
                   "pearson_coefficient"]
    grouping: Literal["overall", "category", "region"]
    periods: tuple[Period, ...]
    selection: Literal["resolution_date", "opening_date"]
    sample_sizes: tuple[int, ...]
    population_sizes: tuple[int, ...]
    excluded_classification_counts: tuple[int, ...]
    sample_unit: Literal["completed_requests", "region_days"]
    completed_request_counts: tuple[int, ...]
    result_status: str
    method: str
    warnings: tuple[str, ...]


class VerifiedFinding(Contract):
    finding_id: str
    evidence: EvidenceFact
    observation: str
    investigation_topic: Topic
    investigation: str
    limitations: tuple[str, ...]


class QuarantinedFinding(Contract):
    item_index: int
    code: str


class FindingsReport(Contract):
    run_id: str
    dataset_id: str
    status: Literal["completed", "partial", "rejected", "empty"]
    accepted: tuple[VerifiedFinding, ...]
    quarantined: tuple[QuarantinedFinding, ...]
    code: str | None = None


class EvidenceError(ValueError):
    """Sanitized failure of the application evidence boundary."""


def _units(metric):
    if metric == "pair_count":
        return "region_days"
    if metric in COUNTS:
        return "completed_requests"
    if metric.endswith("_percent"):
        return "percent"
    return "pearson_coefficient" if metric == "pearson_r" else "elapsed_calendar_hours"


def build_evidence_catalog(report: RunReport) -> tuple[EvidenceFact, ...]:
    """Project approved scalars from this run; never resolve arbitrary JSON paths."""
    try:
        if not isinstance(report, RunReport):
            raise ValueError("Application report required")
        report = RunReport.model_validate(report.model_dump(mode="json"))
        if len(report.outcomes) > 6 or len({s.step_id for s in report.outcomes}) != len(report.outcomes):
            raise ValueError("Invalid run evidence")
        facts = []
        seen = set()
        for step in report.outcomes:
            if step.result is None:
                if step.status in ("completed", "partial"):
                    raise ValueError("Missing result")
                continue
            if step.status not in ("completed", "partial"):
                raise ValueError("Ineligible result")
            result = _validated_result(step.result, RESULT_TYPES[step.call.operation],
                                       step.call, report.dataset_id)
            children = ((result.baseline, result.comparison) if isinstance(result, ComparisonResult) else
                        ((result.overall.baseline, result.overall.comparison) +
                         ((result.categories.baseline, result.categories.comparison)
                          if result.categories is not None else ()))
                        if isinstance(result, DecompositionResult) else ())
            if any(child.dataset_id != report.dataset_id for child in children):
                raise ValueError("Cross-dataset child evidence")
            if isinstance(result, DecompositionResult) and (
                result.overall.dataset_id != report.dataset_id or
                result.overall.baseline.grouping != "overall" or
                result.overall.comparison.grouping != "overall" or
                (result.categories is not None and (
                    result.categories.dataset_id != report.dataset_id or
                    result.categories.baseline.grouping != "category" or
                    result.categories.comparison.grouping != "category" or
                    result.categories.baseline.period != result.overall.baseline.period or
                    result.categories.comparison.period != result.overall.comparison.period))):
                raise ValueError("Inconsistent decomposition scope")
            if step.status != ("completed" if result.status == "complete" else "partial"):
                raise ValueError("Inconsistent step status")

            def add(metric, group, value, grouping, periods, samples, populations, exclusions,
                    warnings, selection="resolution_date", request_counts=None):
                reference = EvidenceReference(result_id=result.result_id, metric=metric, group=group)
                key = (result.result_id, metric, group)
                if key in seen:
                    raise ValueError("Ambiguous evidence")
                seen.add(key)
                if value is not None and not isfinite(value):
                    raise ValueError("Nonfinite evidence")
                payload = {"dataset_id": report.dataset_id, "reference": reference.model_dump(mode="json"),
                           "method": result.method}
                facts.append(EvidenceFact(evidence_id=_identity("evidence-", payload), step_id=step.step_id,
                    reference=reference, dataset_id=report.dataset_id, value=value, units=_units(metric),
                    grouping=grouping, periods=periods, selection=selection, sample_sizes=samples,
                    population_sizes=populations, excluded_classification_counts=exclusions,
                    sample_unit="region_days" if selection == "opening_date" else "completed_requests",
                    completed_request_counts=request_counts if request_counts is not None else samples,
                    result_status=result.status, method=result.method,
                    warnings=tuple(dict.fromkeys(warnings))))

            if isinstance(result, SummaryResult):
                for row in result.rows:
                    for metric in ("sample_size", "mean_hours", "median_hours"):
                        add(metric, row.group, getattr(row, metric), result.grouping, (result.period,),
                            (row.sample_size,), (result.population_size,),
                            (result.excluded_classification_count,),
                            result.warnings + ("COMPLETED_REQUESTS_ONLY",))
            elif isinstance(result, ComparisonResult):
                for row in result.rows:
                    for metric in ("baseline_count", "comparison_count", "count_delta", "mean_change_hours",
                                   "median_change_hours", "mean_change_percent", "median_change_percent"):
                        add(metric, row.group, getattr(row, metric), result.baseline.grouping,
                            (result.baseline.period, result.comparison.period),
                            (row.baseline_count, row.comparison_count),
                            (result.baseline.population_size, result.comparison.population_size),
                            (result.baseline.excluded_classification_count,
                             result.comparison.excluded_classification_count),
                            result.warnings + ("COMPLETED_REQUESTS_ONLY", "RAW_COUNTS_NOT_RATES"))
            elif isinstance(result, DecompositionResult):
                periods = (result.overall.baseline.period, result.overall.comparison.period)
                populations = (result.overall.baseline.population_size, result.overall.comparison.population_size)
                warnings = result.warnings + ("COMPLETED_REQUESTS_ONLY", "ARITHMETIC_ATTRIBUTION_NOT_CAUSATION",
                                              "MEAN_ONLY")
                for metric in ("observed_mean_change_hours", "mix_contribution_hours",
                               "within_category_contribution_hours"):
                    add(metric, "All", getattr(result, metric), "overall", periods, populations,
                        populations, (0, 0), warnings)
                for row in result.rows:
                    for metric in ("mix_contribution_hours", "within_category_contribution_hours"):
                        exclusions = ((result.categories.baseline.excluded_classification_count,
                                       result.categories.comparison.excluded_classification_count)
                                      if result.categories is not None else populations)
                        add(metric, row.category, getattr(row, metric), "category", periods,
                            (row.baseline_count, row.comparison_count), populations, exclusions, warnings)
            else:
                warnings = result.warnings + ("ASSOCIATION_NOT_CAUSATION", "COMPLETED_REQUEST_SELECTION_BIAS",
                    "CASE_MIX_NOT_ADJUSTED", "TEMPORAL_DEPENDENCE_NOT_ADJUSTED", "EQUAL_REGION_DAY_WEIGHT",
                    "POOLED_REGIONS_MAY_CONFOUND", "NO_SIGNIFICANCE_TEST")
                for row in result.associations:
                    for metric in ("pearson_r", "pair_count"):
                        add(metric, row.group, getattr(row, metric), "overall" if row.group == "All" else "region",
                            (result.opening_period,), (row.pair_count,), (result.eligible_region_days if row.group == "All" else result.opening_period.days,),
                            (), warnings, selection="opening_date",
                            request_counts=(row.completed_request_count,))
        return tuple(facts)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise EvidenceError("INVALID_RUN_EVIDENCE") from None


def _topic_supported(topic, fact):
    if topic == "none":
        return True
    metric = fact.reference.metric
    if topic == "category_mix":
        return metric == "mix_contribution_hours"
    if topic == "category_handling":
        return metric == "within_category_contribution_hours" or (
            fact.grouping == "category" and metric in ("mean_change_hours", "median_change_hours"))
    if topic == "regional_process":
        return fact.grouping == "region" and metric in ("mean_change_hours", "median_change_hours")
    return metric == "pearson_r"


def _observation(fact):
    # Values and scope come exclusively from application evidence.
    value = str(fact.value) if fact.reference.metric in COUNTS else format(fact.value, ".12g")
    periods = "; ".join(f"{p.start.isoformat()} to {p.end.isoformat()}" for p in fact.periods)
    label = fact.reference.metric.replace("_", " ")
    return (f"{fact.grouping}={fact.reference.group}: {label} = {value} {fact.units}. "
            f"Selection: {fact.selection}; periods (baseline then comparison when two): {periods}. "
            f"Sample sizes: {fact.sample_sizes} {fact.sample_unit}.")


def _draft_items(raw):
    if isinstance(raw, str):
        if len(raw.encode()) > MAX_DRAFT_BYTES:
            raise ValueError("Too large")
        raw = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if not isinstance(raw, dict) or set(raw) != {"findings"}:
        raise ValueError("Invalid envelope")
    if len(json.dumps(raw, allow_nan=False).encode()) > MAX_DRAFT_BYTES:
        raise ValueError("Too large")
    items = raw["findings"]
    if not isinstance(items, list) or len(items) > MAX_FINDINGS:
        raise ValueError("Invalid list")
    return items


def verify_findings(report: RunReport, raw) -> FindingsReport:
    catalog = build_evidence_catalog(report)
    lookup = {(f.reference.result_id, f.reference.metric, f.reference.group): f for f in catalog}
    try:
        items = _draft_items(raw)
    except (ValueError, TypeError, RecursionError):
        return FindingsReport(run_id=report.run_id, dataset_id=report.dataset_id, status="rejected",
                              accepted=(), quarantined=(), code="INVALID_FINDINGS_ENVELOPE")
    accepted, quarantined, seen = [], [], set()
    for index, raw_item in enumerate(items):
        try:
            draft = FindingDraft.model_validate(raw_item)
        except (ValueError, TypeError):
            quarantined.append(QuarantinedFinding(item_index=index, code="INVALID_FINDING_SCHEMA"))
            continue
        key = (draft.reference.result_id, draft.reference.metric, draft.reference.group)
        fact = lookup.get(key)
        code = None
        if fact is None:
            code = "UNKNOWN_EVIDENCE"
        elif fact.value is None:
            code = "UNDEFINED_METRIC"
        elif not (draft.claimed_value == fact.value if draft.reference.metric in COUNTS else
                  isclose(draft.claimed_value, fact.value, rel_tol=REL_TOL, abs_tol=ABS_TOL)):
            code = "VALUE_MISMATCH"
        elif not _topic_supported(draft.investigation, fact):
            code = "UNSUPPORTED_INVESTIGATION"
        elif key in seen:
            code = "DUPLICATE_FINDING"
        if code:
            quarantined.append(QuarantinedFinding(item_index=index, code=code))
            continue
        seen.add(key)
        investigation = TOPICS[draft.investigation]
        if investigation:
            investigation = "Follow-up investigation: " + investigation + " This is not an established cause."
        accepted.append(VerifiedFinding(
            finding_id=_identity("finding-", {"evidence_id": fact.evidence_id,
                                            "topic": draft.investigation, "method": "finding-v1"}),
            evidence=fact, observation=_observation(fact), investigation_topic=draft.investigation,
            investigation=investigation, limitations=fact.warnings))
    status = "partial" if accepted and quarantined else (
        "completed" if accepted else ("rejected" if quarantined else "empty"))
    return FindingsReport(run_id=report.run_id, dataset_id=report.dataset_id, status=status,
                          accepted=tuple(accepted), quarantined=tuple(quarantined))


def deterministic_findings(report: RunReport) -> FindingsReport:
    """Bounded factual fallback; no model inference and no causal interpretation."""
    facts = build_evidence_catalog(report)
    priority = {"mean_change_hours": 0, "observed_mean_change_hours": 1, "mix_contribution_hours": 2,
                "within_category_contribution_hours": 3, "pearson_r": 4, "mean_hours": 5}
    selected = sorted((f for f in facts if f.value is not None),
                      key=lambda f: (priority.get(f.reference.metric, 10), f.step_id,
                                     f.reference.group, f.reference.metric))[:MAX_FINDINGS]
    return verify_findings(report, {"findings": [
        {"reference": f.reference.model_dump(mode="json"), "claimed_value": f.value}
        for f in selected
    ]})
