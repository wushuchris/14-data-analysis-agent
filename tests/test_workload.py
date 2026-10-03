from datetime import datetime, timedelta, timezone
import json
import pytest
from pydantic import ValidationError
import service_analysis.workload as workload
from service_analysis.workload import analyze_workload, AssociationPolicy
from service_analysis.analysis import Period, CalculationMismatch
from service_analysis.quality import prepare_dataset
from service_analysis.schemas import Coverage

def fixture(x=(1, 2, 3, 4, 5), y=(1, 2, 3, 4, 5), regions=("North",), counts=None,
            staffing=1, no_request_days=()):
    rows, operations = [], []
    counts = counts or [1] * len(x)
    for index, (incoming, hours) in enumerate(zip(x, y)):
        opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
        for region in regions:
            operations.append(dict(date=opened.date().isoformat(), region=region,
                                   incoming_requests=incoming, staffed_hours=staffing))
            if index in no_request_days:
                continue
            for _ in range(counts[index]):
                rows.append(dict(request_id=f"REQ-{len(rows)}", opened_at=opened.isoformat(),
                    resolved_at=(opened + timedelta(hours=hours)).isoformat(),
                    category="Routine", region=region))
    end = (datetime(2026, 1, 1) + timedelta(days=len(x) - 1)).date().isoformat()
    coverage = Coverage(resolution_start="2026-01-01", resolution_end="2026-03-31",
                        operations_start="2026-01-01", operations_end=end, regions=regions)
    return rows, operations, coverage, Period(start="2026-01-01", end=end)

def analyze(*args, **kwargs):
    rows, ops, coverage, period = fixture(*args, **kwargs)
    return analyze_workload(prepare_dataset(rows, ops, coverage), period)

@pytest.mark.parametrize("y,expected", [
    ((1, 2, 3, 4, 5), 1), ((5, 4, 3, 2, 1), -1), ((1, 4, 0, 4, 1), 0)])
def test_known_positive_negative_zero_correlations(y, expected):
    result = analyze(y=y)
    assert result.status == "complete"
    assert result.associations[0].pearson_r == pytest.approx(expected, abs=1e-9)
    assert result.associations[1].pearson_r == pytest.approx(expected, abs=1e-9)
    assert result.paired_region_days == 5
    assert result.selected_completed_requests == 5

def test_daily_pair_arithmetic():
    result = analyze(staffing=2)
    assert [row.requests_per_staffed_hour for row in result.pairs] == [0.5, 1, 1.5, 2, 2.5]
    assert result.pairs[0].mean_resolution_hours == 1
    assert result.pair_sql_verified

def test_equal_day_weight_does_not_replicate_workload():
    result = analyze(counts=[100, 1, 1, 1, 1], y=(5, 4, 3, 2, 1))
    assert result.associations[0].pair_count == 5
    assert result.associations[0].completed_request_count == 104
    assert result.associations[0].pearson_r == pytest.approx(-1)

def test_multiple_requests_are_averaged_within_day():
    rows, ops, coverage, period = fixture()
    extra = dict(rows[0], request_id="EXTRA", resolved_at="2026-01-01T03:00:00Z")
    result = analyze_workload(prepare_dataset(rows + [extra], ops, coverage), period)
    assert result.pairs[0].completed_request_count == 2
    assert result.pairs[0].mean_resolution_hours == 2

@pytest.mark.parametrize("x,y", [((1, 1, 1, 1, 1), (1, 2, 3, 4, 5)),
                                ((1, 2, 3, 4, 5), (2, 2, 2, 2, 2)),
                                ((0, 0, 0, 0, 0), (1, 2, 3, 4, 5))])
def test_constant_inputs_are_undefined(x, y):
    result = analyze(x=x, y=y)
    assert result.status == "unavailable"
    assert result.associations[0].status == "constant_input"
    assert result.associations[0].pearson_r is None

def test_small_sample_withholds_coefficient():
    result = analyze(x=(1, 2), y=(1, 2))
    assert result.associations[0].status == "insufficient_pairs"
    assert result.associations[0].pearson_r is None

def test_explicit_pair_policy():
    rows, ops, coverage, period = fixture(x=(1, 2, 3), y=(1, 2, 3))
    result = analyze_workload(prepare_dataset(rows, ops, coverage), period,
                              AssociationPolicy(minimum_pairs=3))
    assert result.associations[0].pearson_r == pytest.approx(1)
    assert result.minimum_pairs == 3

def test_policy_cannot_publish_two_point_correlation():
    with pytest.raises(ValidationError):
        AssociationPolicy(minimum_pairs=2)

def test_unpaired_days_are_not_zero_resolution():
    result = analyze(no_request_days=(0,))
    assert result.eligible_region_days == 5
    assert result.paired_region_days == 4
    assert result.unpaired_region_days == 1
    assert result.status == "unavailable"
    assert "REGION_DAYS_WITHOUT_COMPLETED_COHORT_EXCLUDED" in result.warnings

def test_empty_opening_window():
    rows, ops, coverage, _ = fixture(no_request_days=(0, 1, 2, 3))
    result = analyze_workload(prepare_dataset(rows, ops, coverage),
                              Period(start="2026-01-01", end="2026-01-04"))
    assert result.pairs == ()
    assert result.selected_completed_requests == 0
    assert result.unpaired_region_days == 4
    assert result.associations[0].pearson_r is None

@pytest.mark.parametrize("problem", ["missing_day", "zero_staffing", "missing_staffing", "invalid_count"])
def test_quality_failures_block_workload(problem):
    rows, ops, coverage, period = fixture()
    if problem == "missing_day":
        ops.pop()
    elif problem == "zero_staffing":
        ops[0]["staffed_hours"] = 0
    elif problem == "missing_staffing":
        ops[0]["staffed_hours"] = None
    else:
        ops[0]["incoming_requests"] = -1
    with pytest.raises(ValueError):
        analyze_workload(prepare_dataset(rows, ops, coverage), period)

def test_opening_window_must_be_covered():
    rows, ops, coverage, _ = fixture()
    with pytest.raises(ValueError):
        analyze_workload(prepare_dataset(rows, ops, coverage),
                         Period(start="2025-12-31", end="2026-01-05"))

def test_opening_date_not_resolution_date_selects_cohort():
    rows, ops, coverage, _ = fixture(y=(48, 48, 48, 48, 48))
    result = analyze_workload(prepare_dataset(rows, ops, coverage),
                              Period(start="2026-01-01", end="2026-01-01"))
    assert result.selected_completed_requests == 1
    assert result.pairs[0].mean_resolution_hours == 48
    assert result.pairs[0].opening_date.isoformat() == "2026-01-01"

def test_separate_regional_associations():
    result = analyze(regions=("North", "South"))
    groups = {row.group: row for row in result.associations}
    assert groups["All"].pair_count == 10
    assert groups["North"].pair_count == groups["South"].pair_count == 5
    assert all(row.pearson_r == pytest.approx(1) for row in groups.values())
    assert "POOLED_REGIONS_MAY_CONFOUND" in result.warnings

def test_sql_pair_mismatch_blocks_result(monkeypatch):
    monkeypatch.setattr(workload, "_sql_pairs", lambda *args: ())
    with pytest.raises(CalculationMismatch):
        analyze()

def test_independent_correlation_mismatch_blocks_result(monkeypatch):
    monkeypatch.setattr(workload, "correlation", lambda *args: 0)
    with pytest.raises(CalculationMismatch):
        analyze()

def test_numerically_invalid_ratio_fails_explicitly():
    rows, ops, coverage, period = fixture(staffing=1e-320)
    with pytest.raises(ValueError, match="numerical limits"):
        analyze_workload(prepare_dataset(rows, ops, coverage), period)

def test_sql_integer_overflow_fails_explicitly():
    rows, ops, coverage, period = fixture()
    ops[0]["incoming_requests"] = 2**64
    with pytest.raises(ValueError, match="numerical limits"):
        analyze_workload(prepare_dataset(rows, ops, coverage), period)

def test_required_limitations_and_export():
    result = analyze()
    expected = {"ASSOCIATION_NOT_CAUSATION", "COMPLETED_REQUEST_SELECTION_BIAS",
                "CASE_MIX_NOT_ADJUSTED", "TEMPORAL_DEPENDENCE_NOT_ADJUSTED",
                "EQUAL_REGION_DAY_WEIGHT"}
    assert expected <= set(result.warnings)
    payload = result.model_dump_json()
    assert "REQ-0" not in payload and "NaN" not in payload
    assert json.loads(payload)["weighting"] == "equal_region_day_weight"

def test_reproducible_identity_tracks_policy_and_window():
    rows, ops, coverage, period = fixture()
    source = prepare_dataset(rows, ops, coverage)
    first = analyze_workload(source, period)
    assert first.result_id == analyze_workload(source, period).result_id
    assert first.result_id != analyze_workload(source, period, AssociationPolicy(minimum_pairs=3)).result_id
    assert first.result_id != analyze_workload(source,
        Period(start="2026-01-01", end="2026-01-04")).result_id

def test_null_export_for_unavailable_association():
    result = analyze(x=(1, 2), y=(1, 2))
    assert json.loads(result.model_dump_json())["associations"][0]["pearson_r"] is None

def test_unknown_policy_rejected():
    rows, ops, coverage, period = fixture()
    with pytest.raises(ValueError):
        analyze_workload(prepare_dataset(rows, ops, coverage), period, {"minimum_pairs": 2})
