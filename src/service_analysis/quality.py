"""Dataset preparation and explicit analysis-readiness gates."""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal
from pydantic import Field, ValidationError
from service_analysis.schemas import Contract, CompletedRequest, Coverage, DailyOperations, QualityPolicy

Scope = Literal["resolution", "category", "region", "workload"]


class QualityIssue(Contract):
    code: str
    severity: Literal["warning", "blocking"]
    table: Literal["requests", "operations"]
    row_numbers: tuple[int, ...] = ()
    scopes: tuple[Scope, ...]
    count: int = Field(default=1, ge=1)


class TableCounts(Contract):
    input_rows: int
    accepted_rows: int
    excluded_rows: int
    duplicate_rows: int


class DatasetProfile(Contract):
    requests: TableCounts
    operations: TableCounts
    resolution_ready: bool
    category_ready: bool
    region_ready: bool
    workload_ready: bool
    missing_category_count: int
    missing_region_count: int
    issues: tuple[QualityIssue, ...]


@dataclass(frozen=True)
class PreparedDataset:
    coverage: Coverage
    profile: DatasetProfile
    requests: tuple[CompletedRequest, ...]
    operations: tuple[DailyOperations, ...]
    policy: QualityPolicy
    # Private snapshot is intentionally excluded from representations and profile exports.
    _original: dict = field(repr=False, compare=False)

    def original_snapshot(self):
        """Return a copy; never expose the retained source through public audit output."""
        return deepcopy(self._original)

    def require(self, scope: Scope):
        if scope not in ("resolution", "category", "region", "workload"):
            raise ValueError("Unknown analysis scope")
        if not getattr(self.profile, scope + "_ready"):
            raise ValueError("Analysis scope is blocked: " + scope)

    def category_requests(self):
        self.require("category")
        return tuple(row for row in self.requests if row.category is not None)

    def region_requests(self):
        self.require("region")
        return tuple(row for row in self.requests if row.region is not None)


def prepare_dataset(requests: list[dict], operations: list[dict], coverage: Coverage,
                    policy: QualityPolicy | None = None) -> PreparedDataset:
    """Prepare already-decoded records. Parsing files is a separate boundary.

    Invalid rows conservatively block their table's analyses. No partial-valid
    population is silently published after a blocking error.
    """
    policy = policy or QualityPolicy()
    if not isinstance(coverage, Coverage):
        raise ValueError("Validated Coverage is required")
    if not isinstance(requests, list) or not isinstance(operations, list):
        raise ValueError("Inputs must be lists of records")
    if max(len(requests), len(operations)) > policy.max_rows_per_table:
        raise ValueError("Dataset exceeds configured row limit")
    original = deepcopy({"requests": requests, "operations": operations})
    issues = []

    def issue(code, table, scopes, severity="blocking", rows=(), count=1):
        issues.append(QualityIssue(code=code, table=table, scopes=scopes,
                                  severity=severity, row_numbers=rows, count=count))

    def validate_table(raw_rows, model, table, keys, scopes):
        seen = {}
        candidates = []
        duplicates = 0
        blocked = False
        for index, raw in enumerate(raw_rows, 1):
            if not isinstance(raw, dict):
                issue("INVALID_ROW", table, scopes, rows=(index,))
                blocked = True
                continue
            # Detect identity collisions before validation, including invalid siblings.
            key = tuple(raw.get(name) for name in keys)
            key_valid = all(isinstance(value, str) and value.strip() for value in key)
            # Operations dates may already be date objects; the schema handles parsing.
            if table == "operations":
                key = (str(raw.get("date")), raw.get("region"))
                key_valid = isinstance(key[1], str) and raw.get("date") is not None
            if key_valid and key in seen:
                prior_index, prior = seen[key]
                if raw == prior:
                    duplicates += 1
                    issue("EXACT_DUPLICATE", table, scopes, "warning", (prior_index, index))
                    continue
                issue("CONFLICTING_DUPLICATE", table, scopes, rows=(prior_index, index))
                blocked = True
            elif key_valid:
                seen[key] = (index, deepcopy(raw))
            try:
                row = model.model_validate(raw)
            except ValidationError:
                # Never publish raw values or Pydantic error input payloads.
                issue("INVALID_ROW", table, scopes, rows=(index,))
                blocked = True
                continue
            if table == "requests":
                in_bounds = coverage.resolution_start <= row.resolved_at.date() <= coverage.resolution_end
                in_region = row.region is None or row.region in coverage.regions
            else:
                in_bounds = coverage.operations_start <= row.date <= coverage.operations_end
                in_region = row.region in coverage.regions
            if not in_bounds or not in_region:
                issue("OUTSIDE_COVERAGE", table, scopes, rows=(index,))
                blocked = True
                continue
            candidates.append(row)
        # Catch duplicate keys after normalization, e.g. date string vs date object.
        normalized = {}
        for row in candidates:
            key = (row.request_id,) if table == "requests" else (row.date, row.region)
            if key in normalized:
                issue("NORMALIZED_DUPLICATE", table, scopes)
                blocked = True
            normalized[key] = row
        if not raw_rows:
            issue("EMPTY_TABLE", table, scopes)
            blocked = True
        accepted = () if blocked else tuple(candidates)
        counts = TableCounts(input_rows=len(raw_rows), accepted_rows=len(accepted),
                             excluded_rows=len(raw_rows) - duplicates - len(accepted),
                             duplicate_rows=duplicates)
        return accepted, counts, blocked

    resolution_scopes = ("resolution", "category", "region", "workload")
    valid_requests, request_counts, request_blocked = validate_table(
        requests, CompletedRequest, "requests", ("request_id",), resolution_scopes)
    valid_ops, ops_counts, ops_blocked = validate_table(
        operations, DailyOperations, "operations", ("date", "region"), ("workload",))

    missing_category = sum(row.category is None for row in valid_requests)
    missing_region = sum(row.region is None for row in valid_requests)
    if missing_category:
        issue("MISSING_CATEGORY", "requests", ("category",), "warning", count=missing_category)
    if missing_region:
        issue("MISSING_REGION", "requests", ("region", "workload"), "warning", count=missing_region)

    for attribute, scope in (("category", "category"), ("region", "region")):
        counts = Counter(getattr(row, attribute) for row in valid_requests
                         if getattr(row, attribute) is not None)
        tiny = sum(n < policy.tiny_group_threshold for n in counts.values())
        if tiny:
            issue("TINY_GROUP", "requests", (scope,), "warning", count=tiny)

    ops_lookup = {(row.date, row.region): row for row in valid_ops}
    expected = set()
    day = coverage.operations_start
    while day <= coverage.operations_end:
        expected.update((day, region) for region in coverage.regions)
        day += timedelta(days=1)
    missing_days = len(expected - ops_lookup.keys())
    if missing_days:
        issue("MISSING_OPERATIONS_COVERAGE", "operations", ("workload",), count=missing_days)
    unavailable_staffing = sum(row.staffed_hours is None or row.staffed_hours == 0 for row in valid_ops)
    if unavailable_staffing:
        issue("UNAVAILABLE_STAFFING", "operations", ("workload",), count=unavailable_staffing)
    missing_links = sum(row.region is None or (row.opened_at.date(), row.region) not in ops_lookup
                        for row in valid_requests)
    if missing_links:
        issue("MISSING_WORKLOAD_LINK", "requests", ("workload",), count=missing_links)
    resolution_ready = not request_blocked
    category_ready = resolution_ready and any(row.category is not None for row in valid_requests)
    region_ready = resolution_ready and any(row.region is not None for row in valid_requests)
    workload_ready = (resolution_ready and not ops_blocked and not missing_days
                      and not unavailable_staffing and not missing_links)
    profile = DatasetProfile(
        requests=request_counts, operations=ops_counts,
        resolution_ready=resolution_ready, category_ready=category_ready,
        region_ready=region_ready, workload_ready=workload_ready,
        missing_category_count=missing_category, missing_region_count=missing_region,
        issues=tuple(issues))
    return PreparedDataset(coverage, profile, valid_requests, valid_ops, policy, original)
