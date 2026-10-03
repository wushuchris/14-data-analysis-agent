from datetime import datetime, timedelta, timezone
import json
import pytest
from pydantic import ValidationError
import service_analysis.analysis as analysis
from service_analysis.analysis import Period, StatsRow, summarize_resolution, compare_periods, CalculationMismatch
from service_analysis.quality import prepare_dataset
from service_analysis.schemas import Coverage, QualityPolicy

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")
MAR = Period(start="2026-03-01", end="2026-03-31")

def records(specs):
    rows = []
    for index, (month, hours, category, region) in enumerate(specs):
        resolved = datetime(2026, month, 15, tzinfo=timezone.utc)
        rows.append(dict(request_id=f"REQ-{index}", opened_at=(resolved - timedelta(hours=hours)).isoformat(),
                         resolved_at=resolved.isoformat(), category=category, region=region))
    return rows

def prepared(specs, policy=None):
    return prepare_dataset(records(specs), [], Coverage(
        resolution_start="2026-01-01", resolution_end="2026-03-31",
        operations_start="2026-01-01", operations_end="2026-01-01"), policy)

def simple(hours, month=1):
    return [(month, value, "Routine", "North") for value in hours]

@pytest.mark.parametrize("hours,mean,median", [
    ([10], 10, 10), ([0, 2, 4, 10], 4, 3), ([1, 3, 20], 8, 3),
    ([1, 1, 1, 9], 3, 1), ([0.25, 0.5, 0.75], 0.5, 0.5),
    ([0, 0], 0, 0), ([1000, 2000], 1500, 1500)])
def test_hand_calculated_summaries(hours, mean, median):
    result = summarize_resolution(prepared(simple(hours)), JAN)
    row = result.rows[0]
    assert row.sample_size == len(hours)
    assert row.mean_hours == pytest.approx(mean)
    assert row.median_hours == pytest.approx(median)
    assert result.sql_verified

def test_period_filters():
    result = summarize_resolution(prepared(simple([10], 1) + simple([30], 2)), JAN)
    assert result.rows[0].sample_size == 1
    assert result.rows[0].mean_hours == 10

def test_end_date_and_utc_normalization():
    rows = records(simple([10]))
    rows[0]["opened_at"] = "2026-01-31T10:00:00Z"
    rows[0]["resolved_at"] = "2026-02-01T01:00:00+02:00"  # January 31 UTC
    source = prepared(simple([10]))
    dataset = prepare_dataset(rows, [], source.coverage)
    assert summarize_resolution(dataset, JAN).rows[0].mean_hours == 13

@pytest.mark.parametrize("grouping", ["category", "region"])
def test_grouped_known_answers(grouping):
    dataset = prepared([(1, 10, "Routine", "North"), (1, 20, "Routine", "North"),
                        (1, 40, "Complex", "South")])
    result = summarize_resolution(dataset, JAN, grouping)
    groups = {row.group: row for row in result.rows}
    assert sorted(row.mean_hours for row in groups.values()) == [15, 40]
    assert sum(row.sample_size for row in groups.values()) == 3

def test_missing_classification_has_denominator():
    result = summarize_resolution(prepared([(1, 10, "Routine", "North"),
                                           (1, 30, None, "North")]), JAN, "category")
    assert result.population_size == 2
    assert result.included_count == result.excluded_classification_count == 1
    assert result.status == "partial"
    assert result.rows[0].mean_hours == 10

def test_empty_period_is_undefined_not_zero():
    result = summarize_resolution(prepared(simple([10])), FEB)
    assert result.status == "no_observations"
    assert result.rows[0].sample_size == 0
    assert result.rows[0].mean_hours is None
    assert result.rows[0].median_hours is None

def test_empty_grouped_period():
    result = summarize_resolution(prepared(simple([10])), FEB, "category")
    assert result.rows == ()
    assert result.status == "no_observations"

def test_case_mix_oracle_overall_and_groups():
    specs = [(1, 10, "Routine", "North")] * 8 + [(1, 30, "Complex", "North")] * 2
    specs += [(2, 10, "Routine", "North")] * 2 + [(2, 30, "Complex", "North")] * 8
    dataset = prepared(specs)
    overall = compare_periods(dataset, JAN, FEB)
    assert overall.baseline.rows[0].mean_hours == 14
    assert overall.comparison.rows[0].mean_hours == 26
    assert overall.rows[0].mean_change_hours == 12
    assert overall.rows[0].mean_change_percent == pytest.approx(12 / 14 * 100)
    assert overall.rows[0].median_change_hours == 20
    by_category = compare_periods(dataset, JAN, FEB, "category")
    assert all(row.mean_change_hours == 0 for row in by_category.rows)
    # Attribution/decomposition is deliberately not claimed by these tools.

def test_zero_baseline_percent_is_undefined():
    result = compare_periods(prepared(simple([0]) + simple([10], 2)), JAN, FEB)
    assert result.rows[0].mean_change_hours == 10
    assert result.rows[0].mean_change_percent is None
    assert "ZERO_BASELINE_PERCENT_UNDEFINED" in result.warnings

def test_missing_period_group_is_partial():
    result = compare_periods(prepared([(1, 10, "Routine", "North"),
                                      (2, 30, "Complex", "North")]), JAN, FEB, "category")
    assert result.status == "partial"
    assert all(row.mean_change_hours is None for row in result.rows)
    assert {row.count_delta for row in result.rows} == {-1, 1}

def test_no_observations_in_both_periods():
    result = compare_periods(prepared(simple([10], 3)), JAN, FEB)
    assert result.status == "no_observations"
    assert result.rows[0].mean_change_hours is None

def test_missing_one_period():
    result = compare_periods(prepared(simple([10])), JAN, FEB)
    assert result.status == "partial"
    assert result.rows[0].comparison_count == 0
    assert result.rows[0].mean_change_hours is None

def test_unequal_windows_warn_counts_not_rates():
    result = compare_periods(prepared(simple([10]) + simple([20], 2)), JAN, FEB)
    assert "UNEQUAL_PERIOD_LENGTHS_COUNTS_NOT_RATES" in result.warnings

@pytest.mark.parametrize("baseline,comparison", [(JAN, JAN), (FEB, JAN),
    (Period(start="2026-01-01", end="2026-02-01"), FEB)])
def test_invalid_comparison_windows(baseline, comparison):
    with pytest.raises(ValueError):
        compare_periods(prepared(simple([10])), baseline, comparison)

def test_period_outside_coverage():
    with pytest.raises(ValueError):
        summarize_resolution(prepared(simple([10])), Period(start="2025-12-01", end="2026-01-31"))

def test_quality_gate_prevents_calculation():
    rows = records(simple([10]))
    rows[0]["resolved_at"] = "invalid"
    dataset = prepare_dataset(rows, [], prepared(simple([10])).coverage)
    with pytest.raises(ValueError):
        summarize_resolution(dataset, JAN)

def test_injected_grouping_rejected_before_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SQL must not be reached")
    monkeypatch.setattr(analysis, "_sql_rows", forbidden)
    with pytest.raises(ValueError):
        summarize_resolution(prepared(simple([10])), JAN, "category; DROP TABLE requests")

def test_engine_disagreement_blocks_result(monkeypatch):
    monkeypatch.setattr(analysis, "_sql_rows", lambda *args: (
        StatsRow(group="All", sample_size=1, mean_hours=999, median_hours=999),))
    with pytest.raises(CalculationMismatch):
        summarize_resolution(prepared(simple([10])), JAN)

def test_identity_is_reproducible_and_tracks_filters():
    dataset = prepared(simple([10]) + simple([20], 2))
    first = summarize_resolution(dataset, JAN)
    assert first.result_id == summarize_resolution(dataset, JAN).result_id
    assert first.result_id != summarize_resolution(dataset, FEB).result_id
    assert first.result_id != summarize_resolution(dataset, JAN, "category").result_id
    assert first.dataset_id != summarize_resolution(prepared(simple([11])), JAN).dataset_id

def test_identity_stable_for_reordered_valid_input():
    specs = simple([1, 2, 3])
    rows = records(specs)
    original = prepared(specs)
    reverse = prepare_dataset(list(reversed(rows)), [], original.coverage)
    assert summarize_resolution(original, JAN).result_id == summarize_resolution(reverse, JAN).result_id

def test_json_export_has_no_nan_or_raw_records():
    result = compare_periods(prepared(simple([10])), JAN, FEB)
    payload = result.model_dump_json()
    assert "NaN" not in payload and "Infinity" not in payload
    assert "REQ-0" not in payload
    assert json.loads(payload)["rows"][0]["mean_change_hours"] is None

def test_period_group_warnings_follow_policy():
    result = summarize_resolution(prepared(simple([10]), QualityPolicy(tiny_group_threshold=1)), JAN)
    assert "TINY_PERIOD_GROUP" not in result.warnings
    assert "TINY_PERIOD_GROUP" in summarize_resolution(prepared(simple([10])), JAN).warnings

def test_deduplication_is_visible():
    dataset = prepared(simple([10]))
    row = records(simple([10]))[0]
    duplicate = prepare_dataset([row, row], [], dataset.coverage)
    result = summarize_resolution(duplicate, JAN)
    assert result.dataset_duplicate_count == 1
    assert result.included_count == 1
    assert "EXACT_DUPLICATES_REMOVED" in result.warnings

@pytest.mark.parametrize("start,end", [("bad", "2026-01-31"), ("2026-02-01", "2026-01-01")])
def test_invalid_period_schema(start, end):
    with pytest.raises(ValidationError):
        Period(start=start, end=end)

def test_sql_connection_is_query_only_and_closed(monkeypatch):
    real_connect = analysis.sqlite3.connect
    observed = []
    class TracedConnection:
        def __init__(self):
            self.connection = real_connect(":memory:")
            self.connection.set_trace_callback(observed.append)
        def __getattr__(self, name):
            return getattr(self.connection, name)
    monkeypatch.setattr(analysis.sqlite3, "connect", lambda *args: TracedConnection())
    summarize_resolution(prepared(simple([10])), JAN)
    assert any("PRAGMA query_only = ON" in statement for statement in observed)

@pytest.mark.parametrize("hours", [list(range(1, n + 1)) for n in range(1, 11)])
def test_sql_pandas_odd_even_population_parity(hours):
    result = summarize_resolution(prepared(simple(hours)), JAN)
    assert result.rows[0].mean_hours == pytest.approx((len(hours) + 1) / 2)
    assert result.rows[0].median_hours == pytest.approx((len(hours) + 1) / 2)
