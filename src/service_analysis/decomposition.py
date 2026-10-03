"""Symmetric decomposition of observed mean-resolution changes.

This is arithmetic attribution, not a causal estimate.
"""
from math import fsum, isclose
from typing import Literal
from pydantic import Field, model_validator
from service_analysis.analysis import (
    ABS_TOL, REL_TOL, CalculationMismatch, ComparisonResult, Period,
    ResultContract, _identity, compare_periods,
)
from service_analysis.quality import PreparedDataset

METHOD = "symmetric-case-mix-v1"


class CategoryContribution(ResultContract):
    category: Literal["Routine", "Standard", "Complex"]
    baseline_count: int = Field(ge=0)
    comparison_count: int = Field(ge=0)
    baseline_share: float | None = Field(ge=0, le=1)
    comparison_share: float | None = Field(ge=0, le=1)
    baseline_mean_hours: float | None = Field(ge=0)
    comparison_mean_hours: float | None = Field(ge=0)
    mix_contribution_hours: float | None
    within_category_contribution_hours: float | None


class DecompositionResult(ResultContract):
    result_id: str
    dataset_id: str
    method: Literal["symmetric-case-mix-v1"] = METHOD
    overall: ComparisonResult
    categories: ComparisonResult | None
    rows: tuple[CategoryContribution, ...]
    observed_mean_change_hours: float | None
    mix_contribution_hours: float | None
    within_category_contribution_hours: float | None
    reconciliation_residual_hours: float | None
    status: Literal["complete", "incomplete"]
    warnings: tuple[str, ...]
    units: Literal["elapsed_calendar_hours"] = "elapsed_calendar_hours"

    @model_validator(mode="after")
    def publication_boundary(self):
        totals = (self.mix_contribution_hours, self.within_category_contribution_hours,
                  self.reconciliation_residual_hours)
        if self.status == "incomplete":
            if any(value is not None for value in totals) or any(
                row.mix_contribution_hours is not None or row.within_category_contribution_hours is not None
                for row in self.rows
            ):
                raise ValueError("Incomplete decomposition cannot publish contributions")
        else:
            if self.categories is None or not self.rows or self.observed_mean_change_hours is None:
                raise ValueError("Complete decomposition requires category evidence and an observed change")
            if any(value is None for value in totals) or any(
                row.mix_contribution_hours is None or row.within_category_contribution_hours is None
                for row in self.rows
            ):
                raise ValueError("Complete decomposition requires all contributions")
            if not isclose(self.mix_contribution_hours + self.within_category_contribution_hours,
                           self.observed_mean_change_hours, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                raise ValueError("Contributions do not reconcile")
            if self.categories.status != "complete" or self.overall.status != "complete":
                raise ValueError("Complete attribution requires complete period evidence")
            if not isclose(fsum(row.mix_contribution_hours for row in self.rows),
                           self.mix_contribution_hours, rel_tol=REL_TOL, abs_tol=ABS_TOL) or not isclose(
                fsum(row.within_category_contribution_hours for row in self.rows),
                self.within_category_contribution_hours, rel_tol=REL_TOL, abs_tol=ABS_TOL
            ):
                raise ValueError("Category contributions do not reconcile to totals")
            evidence_change = self.overall.rows[0].mean_change_hours
            if evidence_change is None or not isclose(evidence_change, self.observed_mean_change_hours,
                                                      rel_tol=REL_TOL, abs_tol=ABS_TOL):
                raise ValueError("Observed change does not match overall evidence")
        return self


def decompose_case_mix(dataset: PreparedDataset, baseline: Period,
                       comparison: Period) -> DecompositionResult:
    """Decompose only when all observations are classified and groups overlap.

    Missing categories, empty periods, or excluded classifications retain the
    observed overall change but withhold every attribution term.
    """
    overall = compare_periods(dataset, baseline, comparison)
    categories = compare_periods(dataset, baseline, comparison, "category") if dataset.profile.category_ready else None
    warnings = list(overall.warnings) + ["ARITHMETIC_ATTRIBUTION_NOT_CAUSATION", "MEAN_ONLY"]
    if categories is not None:
        warnings.extend(categories.warnings)
        first = {row.group: row for row in categories.baseline.rows}
        second = {row.group: row for row in categories.comparison.rows}
    else:
        first, second = {}, {}
        warnings.append("NO_CATEGORY_EVIDENCE")
    n0 = overall.baseline.population_size
    n1 = overall.comparison.population_size
    groups = sorted(first.keys() | second.keys())
    incomplete = not n0 or not n1 or not groups
    if not n0 or not n1:
        warnings.append("EMPTY_PERIOD_DECOMPOSITION_UNAVAILABLE")
    if categories is not None and (
        categories.baseline.excluded_classification_count or
        categories.comparison.excluded_classification_count
    ):
        incomplete = True
        warnings.append("UNCLASSIFIED_REQUESTS_DECOMPOSITION_UNAVAILABLE")
    if set(first) != set(second):
        incomplete = True
        warnings.append("CATEGORY_SUPPORT_CHANGED")
    rows = []
    for group in groups:
        a, b = first.get(group), second.get(group)
        count0, count1 = (a.sample_size if a else 0), (b.sample_size if b else 0)
        p0, p1 = (count0 / n0 if n0 else None), (count1 / n1 if n1 else None)
        m0, m1 = (a.mean_hours if a else None), (b.mean_hours if b else None)
        mix = within = None
        if not incomplete:
            mix = (p1 - p0) * (m1 + m0) / 2
            within = (m1 - m0) * (p1 + p0) / 2
        rows.append(CategoryContribution(category=group, baseline_count=count0,
            comparison_count=count1, baseline_share=p0, comparison_share=p1,
            baseline_mean_hours=m0, comparison_mean_hours=m1,
            mix_contribution_hours=mix, within_category_contribution_hours=within))
    observed = overall.rows[0].mean_change_hours
    mix_total = within_total = residual = None
    if not incomplete:
        mix_total = fsum(row.mix_contribution_hours for row in rows)
        within_total = fsum(row.within_category_contribution_hours for row in rows)
        reconstructed = mix_total + within_total
        residual = reconstructed - observed
        if not isclose(reconstructed, observed, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            raise CalculationMismatch("Case-mix attribution does not reconcile to observed mean change")
    identity = _identity("decomposition-", {
        "method": METHOD, "overall": overall.result_id,
        "categories": categories.result_id if categories is not None else None,
    })
    return DecompositionResult(result_id=identity, dataset_id=overall.dataset_id,
        overall=overall, categories=categories, rows=tuple(rows),
        observed_mean_change_hours=observed, mix_contribution_hours=mix_total,
        within_category_contribution_hours=within_total,
        reconciliation_residual_hours=residual,
        status="incomplete" if incomplete else "complete",
        warnings=tuple(dict.fromkeys(warnings)))
