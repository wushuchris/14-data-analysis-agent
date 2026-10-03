"""Descriptive workload association using equal-weight region-day pairs."""
from contextlib import closing
from datetime import date
from math import isclose, isfinite
import sqlite3
from statistics import correlation, StatisticsError
from typing import Literal
import pandas as pd
from pydantic import Field, StrictInt, model_validator
from service_analysis.analysis import (
    ABS_TOL, REL_TOL, CalculationMismatch, Period, ResultContract, _dataset_id, _identity,
)
from service_analysis.quality import PreparedDataset
from service_analysis.schemas import Contract, Region

METHOD = "region-day-workload-v1"


class AssociationPolicy(Contract):
    minimum_pairs: StrictInt = Field(default=5, ge=3, le=1000)


class WorkloadPair(ResultContract):
    opening_date: date
    region: Region
    completed_request_count: int = Field(ge=1)
    mean_resolution_hours: float = Field(ge=0)
    incoming_requests: int = Field(ge=0)
    staffed_hours: float = Field(gt=0)
    requests_per_staffed_hour: float = Field(ge=0)


class AssociationRow(ResultContract):
    group: Literal["All", "North", "Central", "South"]
    pair_count: int = Field(ge=0)
    completed_request_count: int = Field(ge=0)
    pearson_r: float | None = Field(ge=-1, le=1)
    status: Literal["available", "insufficient_pairs", "constant_input", "numerically_unavailable"]

    @model_validator(mode="after")
    def coefficient_boundary(self):
        if (self.status == "available") != (self.pearson_r is not None):
            raise ValueError("Only available associations may publish a coefficient")
        return self


class WorkloadResult(ResultContract):
    result_id: str
    dataset_id: str
    method: Literal["region-day-workload-v1"] = METHOD
    opening_period: Period
    minimum_pairs: int
    pairs: tuple[WorkloadPair, ...]
    associations: tuple[AssociationRow, ...]
    eligible_region_days: int
    paired_region_days: int
    unpaired_region_days: int
    selected_completed_requests: int
    status: Literal["complete", "unavailable"]
    warnings: tuple[str, ...]
    pair_sql_verified: Literal[True] = True
    weighting: Literal["equal_region_day_weight"] = "equal_region_day_weight"
    x_units: Literal["incoming_requests_per_staffed_hour"] = "incoming_requests_per_staffed_hour"
    y_units: Literal["elapsed_calendar_hours"] = "elapsed_calendar_hours"


def _pair_data(dataset, period):
    request_records = [(row.opened_at.date().isoformat(), row.region, row.resolution_hours)
                       for row in dataset.requests
                       if period.start <= row.opened_at.date() <= period.end]
    operation_records = [(row.date.isoformat(), row.region, row.incoming_requests, row.staffed_hours)
                         for row in dataset.operations if period.start <= row.date <= period.end]
    for _, _, incoming, staffing in operation_records:
        try:
            ratio = incoming / staffing
        except (OverflowError, ZeroDivisionError):
            raise ValueError("Workload ratio exceeds safe numerical limits") from None
        if incoming > 2**63 - 1 or not isfinite(ratio):
            raise ValueError("Workload value exceeds safe numerical limits")
    return request_records, operation_records


def _pandas_pairs(requests, operations):
    if not requests:
        return ()
    frame = pd.DataFrame(requests, columns=["date", "region", "hours"])
    daily = frame.groupby(["date", "region"], as_index=False).agg(
        completed_request_count=("hours", "size"), mean_resolution_hours=("hours", "mean"))
    ops = pd.DataFrame(operations, columns=["date", "region", "incoming_requests", "staffed_hours"])
    joined = daily.merge(ops, on=["date", "region"], how="left", validate="one_to_one")
    if joined["staffed_hours"].isna().any() or (joined["staffed_hours"] <= 0).any():
        raise ValueError("Missing or invalid workload link")
    joined = joined.sort_values(["date", "region"])
    return tuple(WorkloadPair(opening_date=row.date, region=row.region,
        completed_request_count=int(row.completed_request_count),
        mean_resolution_hours=float(row.mean_resolution_hours),
        incoming_requests=int(row.incoming_requests), staffed_hours=float(row.staffed_hours),
        requests_per_staffed_hour=float(row.incoming_requests / row.staffed_hours))
        for row in joined.itertuples(index=False))


def _sql_pairs(requests, operations):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE requests (date TEXT, region TEXT, hours REAL)")
        connection.execute("CREATE TABLE operations (date TEXT, region TEXT, incoming INTEGER, staffing REAL)")
        connection.executemany("INSERT INTO requests VALUES (?, ?, ?)", requests)
        connection.executemany("INSERT INTO operations VALUES (?, ?, ?, ?)", operations)
        connection.commit()
        connection.execute("PRAGMA query_only = ON")
        values = connection.execute("""
            SELECT r.date, r.region, COUNT(*), AVG(r.hours), o.incoming, o.staffing,
                   CAST(o.incoming AS REAL) / o.staffing
            FROM requests r JOIN operations o ON r.date = o.date AND r.region = o.region
            GROUP BY r.date, r.region, o.incoming, o.staffing
            ORDER BY r.date, r.region
        """).fetchall()
    return tuple(WorkloadPair(opening_date=v[0], region=v[1], completed_request_count=v[2],
        mean_resolution_hours=v[3], incoming_requests=v[4], staffed_hours=v[5],
        requests_per_staffed_hour=v[6]) for v in values)


def _verify_pairs(first, second):
    if len(first) != len(second):
        raise CalculationMismatch("Workload pair populations disagree")
    for a, b in zip(first, second):
        if (a.opening_date, a.region, a.completed_request_count, a.incoming_requests) != (
            b.opening_date, b.region, b.completed_request_count, b.incoming_requests
        ):
            raise CalculationMismatch("Workload pair identities or counts disagree")
        for name in ("mean_resolution_hours", "staffed_hours", "requests_per_staffed_hour"):
            if not isclose(getattr(a, name), getattr(b, name), rel_tol=REL_TOL, abs_tol=ABS_TOL):
                raise CalculationMismatch("Workload pair calculations disagree")


def _association(pairs, group, minimum):
    n = len(pairs)
    status, coefficient = "available", None
    x = [row.requests_per_staffed_hour for row in pairs]
    y = [row.mean_resolution_hours for row in pairs]
    if n < minimum:
        status = "insufficient_pairs"
    elif min(x) == max(x) or min(y) == max(y):
        status = "constant_input"
    else:
        # Positive rescaling preserves Pearson correlation and limits overflow.
        max_x, max_y = max(x), max(y)
        x = [value / max_x for value in x]
        y = [value / max_y for value in y]
        try:
            pandas_r = float(pd.Series(x).corr(pd.Series(y), method="pearson"))
            standard_r = correlation(x, y)
        except (StatisticsError, OverflowError, ZeroDivisionError):
            status = "numerically_unavailable"
        else:
            if not isfinite(pandas_r) or not isfinite(standard_r):
                status = "numerically_unavailable"
            elif not isclose(pandas_r, standard_r, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                raise CalculationMismatch("Independent correlation calculations disagree")
            else:
                coefficient = max(-1.0, min(1.0, pandas_r))  # Floating-point boundary only.
    return AssociationRow(group=group, pair_count=n,
        completed_request_count=sum(row.completed_request_count for row in pairs),
        pearson_r=coefficient, status=status)


def analyze_workload(dataset: PreparedDataset, opening_period: Period,
                     policy: AssociationPolicy | None = None) -> WorkloadResult:
    if not isinstance(dataset, PreparedDataset) or not isinstance(opening_period, Period):
        raise ValueError("Validated dataset and opening Period are required")
    dataset.require("workload")
    if (opening_period.start < dataset.coverage.operations_start or
        opening_period.end > dataset.coverage.operations_end):
        raise ValueError("Opening period lies outside operations coverage")
    policy = policy or AssociationPolicy()
    if not isinstance(policy, AssociationPolicy):
        raise ValueError("Validated AssociationPolicy is required")
    requests, operations = _pair_data(dataset, opening_period)
    pairs = _pandas_pairs(requests, operations)
    _verify_pairs(pairs, _sql_pairs(requests, operations))
    associations = [_association(pairs, "All", policy.minimum_pairs)]
    associations.extend(_association(tuple(row for row in pairs if row.region == region),
                                     region, policy.minimum_pairs)
                        for region in dataset.coverage.regions)
    warnings = [
        "ASSOCIATION_NOT_CAUSATION", "COMPLETED_REQUEST_SELECTION_BIAS",
        "CASE_MIX_NOT_ADJUSTED", "TEMPORAL_DEPENDENCE_NOT_ADJUSTED",
        "EQUAL_REGION_DAY_WEIGHT", "POOLED_REGIONS_MAY_CONFOUND",
    ]
    if len(operations) != len(pairs):
        warnings.append("REGION_DAYS_WITHOUT_COMPLETED_COHORT_EXCLUDED")
    if any(row.completed_request_count < dataset.policy.tiny_group_threshold for row in pairs):
        warnings.append("TINY_DAILY_COHORT")
    if any(row.status == "insufficient_pairs" for row in associations):
        warnings.append("INSUFFICIENT_PAIR_COUNT")
    if any(row.status == "constant_input" for row in associations):
        warnings.append("CONSTANT_INPUT_CORRELATION_UNDEFINED")
    if any(row.status == "numerically_unavailable" for row in associations):
        warnings.append("NUMERICAL_CORRELATION_UNAVAILABLE")
    dataset_id = _dataset_id(dataset)
    identity = _identity("workload-", {"dataset_id": dataset_id, "method": METHOD,
        "opening_period": opening_period.model_dump(mode="json"), "policy": policy.model_dump(mode="json")})
    return WorkloadResult(result_id=identity, dataset_id=dataset_id,
        opening_period=opening_period, minimum_pairs=policy.minimum_pairs,
        pairs=pairs, associations=tuple(associations), eligible_region_days=len(operations),
        paired_region_days=len(pairs), unpaired_region_days=len(operations) - len(pairs),
        selected_completed_requests=len(requests),
        status="complete" if associations[0].status == "available" else "unavailable",
        warnings=tuple(warnings))
