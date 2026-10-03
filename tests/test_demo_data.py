import pytest
from service_analysis.analysis import Period, compare_periods
from service_analysis.decomposition import decompose_case_mix
from service_analysis.demo_data import load_scenario, SCENARIOS
from service_analysis.model_adapter import AnalysisRequest, run_assisted_analysis
from service_analysis.workload import analyze_workload

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")


@pytest.mark.parametrize("name", ["combined", "case_mix", "within_category", "stable", "regional", "duplicates"])
def test_demo_independent_oracles(name):
    data = load_scenario(name)
    expected = SCENARIOS[name]
    comparison = compare_periods(data, JAN, FEB)
    assert comparison.rows[0].mean_change_hours == pytest.approx(expected.expected_mean_change, abs=1e-9)
    assert comparison.baseline.rows[0].mean_hours == 16
    assert comparison.baseline.population_size == comparison.comparison.population_size == 30
    attribution = decompose_case_mix(data, JAN, FEB)
    assert attribution.mix_contribution_hours == pytest.approx(expected.expected_mix, abs=1e-9)
    assert attribution.within_category_contribution_hours == pytest.approx(expected.expected_within, abs=1e-9)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_demo_default_question_has_expected_completion(name):
    result = run_assisted_analysis(load_scenario(name), AnalysisRequest(
        question="drivers", baseline=JAN, comparison=FEB, opening_period=FEB))
    expected = "unavailable" if name == "invalid_records" else (
        "partial" if name in ("missing_category", "missing_staffing") else "complete")
    assert result.report.status == expected
    assert result.model_calls == 0


def test_all_regions_and_categories_and_full_operations_coverage():
    data = load_scenario("combined")
    assert {r.region for r in data.requests} == {"North", "Central", "South"}
    assert {r.category for r in data.requests} == {"Routine", "Standard", "Complex"}
    assert len(data.requests) == 90 and len(data.operations) == 270
    assert data.profile.workload_ready


def test_workload_constructed_positive_oracle():
    result = analyze_workload(load_scenario("workload"), FEB)
    assert all(row.pearson_r == pytest.approx(1, abs=1e-9) for row in result.associations)
    assert result.paired_region_days == 84
    assert result.selected_completed_requests == 84


def test_missing_category_retains_overall_change_but_withholds_attribution():
    data = load_scenario("missing_category")
    result = decompose_case_mix(data, JAN, FEB)
    assert result.observed_mean_change_hours == 16
    assert result.status == "incomplete" and result.mix_contribution_hours is None
    assert data.profile.missing_category_count == 1


def test_missing_staffing_blocks_only_workload():
    data = load_scenario("missing_staffing")
    assert data.profile.resolution_ready and not data.profile.workload_ready
    with pytest.raises(ValueError):
        analyze_workload(data, FEB)


def test_exact_duplicates_disclosed():
    data = load_scenario("duplicates")
    assert data.profile.requests.duplicate_rows == data.profile.operations.duplicate_rows == 1
    assert len(data.requests) == 90 and len(data.operations) == 270


def test_invalid_source_blocks_release_and_unknown_selection():
    data = load_scenario("invalid_records")
    assert not data.requests and not data.profile.resolution_ready
    with pytest.raises(ValueError):
        load_scenario("unknown")


def test_scenarios_are_reproducible_and_snapshots_isolated():
    first, second = load_scenario("combined"), load_scenario("combined")
    assert first.original_snapshot() == second.original_snapshot()
    snapshot = first.original_snapshot()
    snapshot["requests"][0]["request_id"] = "changed-synthetic-value"
    assert second.original_snapshot()["requests"][0]["request_id"] != "changed-synthetic-value"
