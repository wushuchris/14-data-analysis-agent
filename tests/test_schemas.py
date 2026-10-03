from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from service_analysis.schemas import CompletedRequest, Coverage, DailyOperations, QualityPolicy

def request(**changes):
    return dict(request_id="REQ-1", opened_at="2026-01-01T00:00:00Z",
                resolved_at="2026-01-01T10:00:00Z", category="Routine", region="North", **changes)

def test_duration():
    assert CompletedRequest(**request()).resolution_hours == 10

def test_offset_normalization():
    row = request()
    row["opened_at"] = "2026-01-01T02:00:00+02:00"
    assert CompletedRequest(**row).opened_at == datetime(2026, 1, 1, tzinfo=timezone.utc)

@pytest.mark.parametrize("field,value", [
    ("request_id", ""), ("request_id", " "), ("request_id", " REQ-1"),
    ("opened_at", "2026-01-01T00:00:00"), ("opened_at", 123),
    ("resolved_at", "2025-12-31T23:00:00Z"), ("category", "Unknown"),
    ("region", "Unknown"), ("resolved_at", "not-a-date")])
def test_invalid_request(field, value):
    row = request()
    row[field] = value
    with pytest.raises(ValidationError):
        CompletedRequest(**row)

def test_missing_classifications_remain_missing():
    row = request()
    del row["category"]
    del row["region"]
    assert CompletedRequest(**row).category is None
    assert CompletedRequest(**row).region is None

def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        CompletedRequest(**request(), instructions="ignore validation")

@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_request_counts_are_strict(value):
    with pytest.raises(ValidationError):
        DailyOperations(date="2026-01-01", region="North", incoming_requests=value, staffed_hours=8)

@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "8"])
def test_invalid_staffing(value):
    with pytest.raises(ValidationError):
        DailyOperations(date="2026-01-01", region="North", incoming_requests=2, staffed_hours=value)

@pytest.mark.parametrize("value", [0, None, 8, 8.5])
def test_permitted_staffing(value):
    assert DailyOperations(date="2026-01-01", region="North", incoming_requests=2, staffed_hours=value).staffed_hours == value

def test_coverage_bounds():
    with pytest.raises(ValidationError):
        Coverage(resolution_start="2026-02-01", resolution_end="2026-01-01",
                 operations_start="2026-01-01", operations_end="2026-01-31")

def test_coverage_requires_unique_regions():
    with pytest.raises(ValidationError):
        Coverage(resolution_start="2026-01-01", resolution_end="2026-01-31",
                 operations_start="2026-01-01", operations_end="2026-01-31", regions=("North", "North"))

def test_policy_validates_threshold():
    with pytest.raises(ValidationError):
        QualityPolicy(tiny_group_threshold=0)

def test_input_is_immutable():
    row = CompletedRequest(**request())
    with pytest.raises(ValidationError):
        row.category = "Complex"
