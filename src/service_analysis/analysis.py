"""Reproducible resolution summaries and period comparisons.

Pandas computes statistics; independent fixed SQL templates must agree before
a result is returned. No SQL or Python supplied by a model is executed.
"""
from contextlib import closing
from datetime import date
from hashlib import sha256
import json
from math import isclose
import sqlite3
from typing import Literal
import pandas as pd
from pydantic import ConfigDict, Field, field_validator, model_validator
from service_analysis.quality import PreparedDataset
from service_analysis.schemas import Contract, parse_date

Grouping = Literal["overall", "category", "region"]
METHOD = "resolution-summary-v1"
REL_TOL = 1e-10
ABS_TOL = 1e-9


class ResultContract(Contract):
    model_config = ConfigDict(allow_inf_nan=False)


class Period(Contract):
    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def dates(cls, value):
        return parse_date(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Period end precedes start")
        return self

    @property
    def days(self):
        return (self.end - self.start).days + 1


class StatsRow(ResultContract):
    group: str
    sample_size: int = Field(ge=0)
    mean_hours: float | None
    median_hours: float | None

    @model_validator(mode="after")
    def population(self):
        if self.sample_size == 0:
            if self.mean_hours is not None or self.median_hours is not None:
                raise ValueError("Empty population cannot have duration statistics")
        elif self.mean_hours is None or self.median_hours is None:
            raise ValueError("Nonempty population requires duration statistics")
        if any(value is not None and value < 0 for value in (self.mean_hours, self.median_hours)):
            raise ValueError("Duration statistics cannot be negative")
        return self


class SummaryResult(ResultContract):
    result_id: str
    dataset_id: str
    method: str = METHOD
    grouping: Grouping
    period: Period
    rows: tuple[StatsRow, ...]
    population_size: int
    excluded_classification_count: int
    included_count: int
    dataset_excluded_count: int
    dataset_duplicate_count: int
    status: Literal["complete", "partial", "no_observations"]
    warnings: tuple[str, ...]
    sql_verified: Literal[True] = True
    units: Literal["elapsed_calendar_hours"] = "elapsed_calendar_hours"


class ChangeRow(ResultContract):
    group: str
    baseline_count: int
    comparison_count: int
    count_delta: int
    mean_change_hours: float | None
    median_change_hours: float | None
    mean_change_percent: float | None
    median_change_percent: float | None


class ComparisonResult(ResultContract):
    result_id: str
    dataset_id: str
    method: str = "period-comparison-v1"
    baseline: SummaryResult
    comparison: SummaryResult
    rows: tuple[ChangeRow, ...]
    status: Literal["complete", "partial", "no_observations"]
    warnings: tuple[str, ...]


class CalculationMismatch(RuntimeError):
    """Independent calculation engines disagreed; publication is blocked."""


def _identity(prefix, payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return prefix + sha256(encoded.encode()).hexdigest()


def _dataset_id(dataset):
    return _identity("dataset-", {
        "coverage": dataset.coverage.model_dump(mode="json"),
        "profile": dataset.profile.model_dump(mode="json"),
        "policy": dataset.policy.model_dump(mode="json"),
        "requests": [row.model_dump(mode="json") for row in sorted(dataset.requests, key=lambda r: r.request_id)],
        "operations": [row.model_dump(mode="json") for row in sorted(dataset.operations, key=lambda r: (r.date, r.region))],
    })


def _check_arguments(dataset, period, grouping):
    if not isinstance(dataset, PreparedDataset) or not isinstance(period, Period):
        raise ValueError("Validated dataset and Period are required")
    if grouping not in ("overall", "category", "region"):
        raise ValueError("Unsupported grouping")
    dataset.require("resolution" if grouping == "overall" else grouping)
    if period.start < dataset.coverage.resolution_start or period.end > dataset.coverage.resolution_end:
        raise ValueError("Period lies outside declared resolution coverage")


def _records(dataset):
    return [(row.resolved_at.date().isoformat(), row.category, row.region, row.resolution_hours)
            for row in dataset.requests]


def _pandas_rows(records, period, grouping):
    frame = pd.DataFrame(records, columns=["resolved_date", "category", "region", "hours"])
    selected = frame[(frame.resolved_date >= period.start.isoformat()) &
                     (frame.resolved_date <= period.end.isoformat())].copy()
    population = len(selected)
    excluded = 0
    if grouping == "overall":
        selected["bucket"] = "All"
    else:
        excluded = int(selected[grouping].isna().sum())
        selected = selected[selected[grouping].notna()].copy()
        selected["bucket"] = selected[grouping]
    rows = tuple(StatsRow(group=str(group), sample_size=len(values),
                          mean_hours=float(values.mean()), median_hours=float(values.median()))
                 for group, values in selected.groupby("bucket", sort=True)["hours"])
    if grouping == "overall" and not rows:
        rows = (StatsRow(group="All", sample_size=0, mean_hours=None, median_hours=None),)
    return rows, population, excluded


def _sql_rows(records, period, grouping):
    # Every interpolated identifier is selected from this application-owned map.
    expression = {"overall": "'All'", "category": "category", "region": "region"}[grouping]
    nonnull = "" if grouping == "overall" else f" AND {expression} IS NOT NULL"
    query = f"""
        WITH ranked AS (
            SELECT {expression} AS bucket, hours,
                   ROW_NUMBER() OVER (PARTITION BY {expression} ORDER BY hours) AS rn,
                   COUNT(*) OVER (PARTITION BY {expression}) AS n
            FROM requests
            WHERE resolved_date BETWEEN ? AND ? {nonnull}
        )
        SELECT bucket, COUNT(*), AVG(hours),
               AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN hours END)
        FROM ranked GROUP BY bucket ORDER BY bucket
    """
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE requests (resolved_date TEXT, category TEXT, region TEXT, hours REAL)")
        connection.executemany("INSERT INTO requests VALUES (?, ?, ?, ?)", records)
        connection.commit()
        connection.execute("PRAGMA query_only = ON")
        values = connection.execute(query, (period.start.isoformat(), period.end.isoformat())).fetchall()
    rows = tuple(StatsRow(group=row[0], sample_size=row[1], mean_hours=row[2], median_hours=row[3])
                 for row in values)
    if grouping == "overall" and not rows:
        rows = (StatsRow(group="All", sample_size=0, mean_hours=None, median_hours=None),)
    return rows


def _verify(left, right):
    if len(left) != len(right):
        raise CalculationMismatch("Group populations disagree")
    for a, b in zip(left, right):
        if (a.group, a.sample_size) != (b.group, b.sample_size):
            raise CalculationMismatch("Group populations disagree")
        for field in ("mean_hours", "median_hours"):
            x, y = getattr(a, field), getattr(b, field)
            if x is None or y is None:
                if x != y:
                    raise CalculationMismatch("Undefined statistics disagree")
            elif not isclose(x, y, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                raise CalculationMismatch("Duration statistics disagree")


def summarize_resolution(dataset: PreparedDataset, period: Period,
                         grouping: Grouping = "overall") -> SummaryResult:
    _check_arguments(dataset, period, grouping)
    records = _records(dataset)
    rows, population, excluded = _pandas_rows(records, period, grouping)
    _verify(rows, _sql_rows(records, period, grouping))
    included = sum(row.sample_size for row in rows)
    warnings = ["COMPLETED_REQUESTS_ONLY"]
    if included == 0:
        warnings.append("NO_OBSERVATIONS")
    if excluded:
        warnings.append("MISSING_CLASSIFICATION_EXCLUDED")
    if any(0 < row.sample_size < dataset.policy.tiny_group_threshold for row in rows):
        warnings.append("TINY_PERIOD_GROUP")
    if dataset.profile.requests.duplicate_rows:
        warnings.append("EXACT_DUPLICATES_REMOVED")
    dataset_id = _dataset_id(dataset)
    result_id = _identity("summary-", {"dataset_id": dataset_id, "method": METHOD,
        "period": period.model_dump(mode="json"), "grouping": grouping})
    return SummaryResult(result_id=result_id, dataset_id=dataset_id, grouping=grouping,
        period=period, rows=rows, population_size=population,
        excluded_classification_count=excluded, included_count=included,
        dataset_excluded_count=dataset.profile.requests.excluded_rows,
        dataset_duplicate_count=dataset.profile.requests.duplicate_rows,
        status="no_observations" if not included else ("partial" if excluded else "complete"),
        warnings=tuple(warnings))


def _delta(baseline, comparison):
    return None if baseline is None or comparison is None else comparison - baseline


def _percent(baseline, comparison):
    return None if baseline in (None, 0) or comparison is None else (comparison - baseline) / baseline * 100


def compare_periods(dataset: PreparedDataset, baseline: Period, comparison: Period,
                    grouping: Grouping = "overall") -> ComparisonResult:
    _check_arguments(dataset, baseline, grouping)
    _check_arguments(dataset, comparison, grouping)
    if baseline.end >= comparison.start:
        raise ValueError("Baseline must precede a nonoverlapping comparison period")
    first = summarize_resolution(dataset, baseline, grouping)
    second = summarize_resolution(dataset, comparison, grouping)
    a = {row.group: row for row in first.rows}
    b = {row.group: row for row in second.rows}
    rows = []
    warnings = list(dict.fromkeys(first.warnings + second.warnings))
    missing_group = False
    for group in sorted(a.keys() | b.keys()):
        empty = StatsRow(group=group, sample_size=0, mean_hours=None, median_hours=None)
        x, y = a.get(group, empty), b.get(group, empty)
        if not x.sample_size or not y.sample_size:
            missing_group = True
        elif x.mean_hours == 0 or x.median_hours == 0:
            warnings.append("ZERO_BASELINE_PERCENT_UNDEFINED")
        rows.append(ChangeRow(group=group, baseline_count=x.sample_size, comparison_count=y.sample_size,
            count_delta=y.sample_size - x.sample_size,
            mean_change_hours=_delta(x.mean_hours, y.mean_hours),
            median_change_hours=_delta(x.median_hours, y.median_hours),
            mean_change_percent=_percent(x.mean_hours, y.mean_hours),
            median_change_percent=_percent(x.median_hours, y.median_hours)))
    if missing_group:
        warnings.append("MISSING_PERIOD_GROUP")
    if baseline.days != comparison.days:
        warnings.append("UNEQUAL_PERIOD_LENGTHS_COUNTS_NOT_RATES")
    if first.status == second.status == "no_observations":
        status = "no_observations"
    else:
        status = "partial" if missing_group or first.status != "complete" or second.status != "complete" else "complete"
    result_id = _identity("comparison-", {"baseline": first.result_id, "comparison": second.result_id,
                                         "method": "period-comparison-v1"})
    return ComparisonResult(result_id=result_id, dataset_id=first.dataset_id, baseline=first,
        comparison=second, rows=tuple(rows), status=status, warnings=tuple(dict.fromkeys(warnings)))
