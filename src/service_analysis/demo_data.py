"""Reproducible, public-safe service-operations scenarios; no external data."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from service_analysis.schemas import Coverage
from service_analysis.quality import prepare_dataset

REGIONS = ("North", "Central", "South")
MONTHS = {"January": ("2026-01-01", "2026-01-31"),
          "February": ("2026-02-01", "2026-02-28"),
          "March": ("2026-03-01", "2026-03-31")}


@dataclass(frozen=True)
class Scenario:
    label: str
    description: str
    expected_mean_change: float | None
    expected_mix: float | None
    expected_within: float | None


SCENARIOS = {
    "combined": Scenario("Mix and handling change", "More complex requests and slower handling both contribute.",
                         16, 12, 4),
    "case_mix": Scenario("More complex requests", "Category durations stay fixed while the completed-request mix changes.",
                         12, 12, 0),
    "within_category": Scenario("Slower handling", "Category proportions stay fixed while every category takes four hours longer.",
                                4, 0, 4),
    "stable": Scenario("Stable operation", "The completed-request mix and category durations stay unchanged.", 0, 0, 0),
    "regional": Scenario("South region slowdown", "Only the South region takes eight hours longer within each category.",
                          8/3, 0, 8/3),
    "workload": Scenario("Workload association", "Daily demand and completed-cohort duration move together in a constructed example.",
                          None, None, None),
    "missing_category": Scenario("Missing category", "One completed request lacks a category; attribution must be withheld.",
                                  16, None, None),
    "missing_staffing": Scenario("Missing staffing", "One operations day lacks staffing; workload analysis must be blocked.",
                                  16, 12, 4),
    "duplicates": Scenario("Duplicate records", "Exact duplicates must be removed and disclosed before analysis.", 16, 12, 4),
    "invalid_records": Scenario("Invalid timestamp", "An invalid source timestamp blocks request-based analysis.",
                                 None, None, None),
}


def load_scenario(name):
    if name not in SCENARIOS:
        raise ValueError("Unknown demo scenario")
    requests, operations = [], []
    combined = name in ("combined", "missing_category", "missing_staffing", "duplicates", "invalid_records")
    for month in (1, 2, 3):
        for day in range(1, monthrange(2026, month)[1]+1):
            for region in REGIONS:
                phase = (day-1) % 5 + 1
                operations.append(dict(date=date(2026, month, day).isoformat(), region=region,
                    incoming_requests=phase*10, staffed_hours=10))
        for region in REGIONS:
            if name == "workload":
                for day in range(1, monthrange(2026, month)[1]+1):
                    phase = (day-1) % 5 + 1
                    opened = datetime(2026, month, day, tzinfo=timezone.utc)
                    requests.append(dict(request_id=f"demo-{month}-{region}-{day}",
                        opened_at=opened.isoformat(), resolved_at=(opened+timedelta(hours=10+phase*2)).isoformat(),
                        category="Routine", region=region))
                continue
            shifted_mix = month > 1 and (combined or name == "case_mix")
            counts = (2, 3, 5) if shifted_mix else (6, 3, 1)
            index = 0
            for category, count, base_hours in zip(("Routine", "Standard", "Complex"), counts, (10, 20, 40)):
                adjustment = 0
                if month > 1 and (combined or name == "within_category"):
                    adjustment = 4
                if month > 1 and name == "regional" and region == "South":
                    adjustment = 8
                for _ in range(count):
                    opened = datetime(2026, month, 5+index*2, tzinfo=timezone.utc)
                    requests.append(dict(request_id=f"demo-{month}-{region}-{index}",
                        opened_at=opened.isoformat(), resolved_at=(opened+timedelta(hours=base_hours+adjustment)).isoformat(),
                        category=category, region=region))
                    index += 1
    if name == "missing_category":
        next(row for row in requests if row["request_id"] == "demo-2-South-0")["category"] = None
    if name == "missing_staffing":
        next(row for row in operations if row["date"] == "2026-02-10" and row["region"] == "South")["staffed_hours"] = None
    if name == "invalid_records":
        requests[0]["resolved_at"] = "invalid-synthetic-timestamp"
    if name == "duplicates":
        requests.append(dict(requests[0]))
        operations.append(dict(operations[0]))
    coverage = Coverage(resolution_start="2026-01-01", resolution_end="2026-03-31",
                        operations_start="2026-01-01", operations_end="2026-03-31")
    return prepare_dataset(requests, operations, coverage)
