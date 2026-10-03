from datetime import datetime, timedelta, timezone
import json
from math import fsum
import pytest
from pydantic import ValidationError
from service_analysis.analysis import Period
from service_analysis.decomposition import decompose_case_mix, DecompositionResult
from service_analysis.quality import prepare_dataset
from service_analysis.schemas import Coverage

JAN = Period(start="2026-01-01", end="2026-01-31")
FEB = Period(start="2026-02-01", end="2026-02-28")
MAR = Period(start="2026-03-01", end="2026-03-31")

def dataset(specs):
    rows = []
    for month, hours, category, count in specs:
        for _ in range(count):
            resolved = datetime(2026, month, 15, tzinfo=timezone.utc)
            rows.append(dict(request_id=f"REQ-{len(rows)}", category=category, region="North",
                resolved_at=resolved.isoformat(),
                opened_at=(resolved - timedelta(hours=hours)).isoformat()))
    return prepare_dataset(rows, [], Coverage(
        resolution_start="2026-01-01", resolution_end="2026-03-31",
        operations_start="2026-01-01", operations_end="2026-01-01"))

def case_mix():
    return dataset([(1, 10, "Routine", 8), (1, 30, "Complex", 2),
                    (2, 10, "Routine", 2), (2, 30, "Complex", 8)])

def assert_totals(result, observed, mix, within):
    assert result.status == "complete"
    assert result.observed_mean_change_hours == pytest.approx(observed)
    assert result.mix_contribution_hours == pytest.approx(mix)
    assert result.within_category_contribution_hours == pytest.approx(within)
    assert result.reconciliation_residual_hours == pytest.approx(0, abs=1e-9)
    assert fsum(row.mix_contribution_hours for row in result.rows) == pytest.approx(mix)
    assert fsum(row.within_category_contribution_hours for row in result.rows) == pytest.approx(within)

def test_pure_mix_hand_calculated_oracle():
    result = decompose_case_mix(case_mix(), JAN, FEB)
    assert_totals(result, 12, 12, 0)
    rows = {r.category: r for r in result.rows}
    assert rows["Routine"].mix_contribution_hours == pytest.approx(-6)
    assert rows["Complex"].mix_contribution_hours == pytest.approx(18)
    assert "ARITHMETIC_ATTRIBUTION_NOT_CAUSATION" in result.warnings
    assert "MEAN_ONLY" in result.warnings

def test_pure_within_category_slowdown():
    source = dataset([(1, 10, "Routine", 8), (1, 30, "Complex", 2),
                      (2, 14, "Routine", 8), (2, 30, "Complex", 2)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 3.2, 0, 3.2)

def test_combined_oracle():
    source = dataset([(1, 10, "Routine", 8), (1, 30, "Complex", 2),
                      (2, 12, "Routine", 2), (2, 36, "Complex", 8)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 17.2, 13.2, 4)

def test_three_categories_oracle():
    source = dataset([(1, 10, "Routine", 5), (1, 20, "Standard", 3), (1, 40, "Complex", 2),
                      (2, 12, "Routine", 2), (2, 20, "Standard", 3), (2, 40, "Complex", 5)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 9.4, 8.7, 0.7)

def test_no_change():
    source = dataset([(1, 10, "Routine", 3), (1, 30, "Complex", 2),
                      (2, 10, "Routine", 6), (2, 30, "Complex", 4)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 0, 0, 0)

def test_improvement_has_negative_contributions():
    source = dataset([(1, 12, "Routine", 2), (1, 36, "Complex", 8),
                      (2, 10, "Routine", 8), (2, 30, "Complex", 2)])
    assert_totals(decompose_case_mix(source, JAN, FEB), -17.2, -13.2, -4)

def test_one_category():
    source = dataset([(1, 10, "Routine", 2), (2, 15, "Routine", 7)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 5, 0, 5)

def test_zero_duration_categories():
    source = dataset([(1, 0, "Routine", 2), (2, 0, "Routine", 2)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 0, 0, 0)

@pytest.mark.parametrize("specs", [
    [(1, 10, "Routine", 2), (2, 30, "Complex", 2)],
    [(1, 10, "Routine", 2), (1, 30, "Complex", 2), (2, 10, "Routine", 2)],
    [(1, 10, "Routine", 2), (2, 10, "Routine", 2), (2, 30, "Complex", 2)],
])
def test_changed_category_support_withholds_all_attribution(specs):
    result = decompose_case_mix(dataset(specs), JAN, FEB)
    assert result.status == "incomplete"
    assert "CATEGORY_SUPPORT_CHANGED" in result.warnings
    assert result.observed_mean_change_hours is not None
    assert result.mix_contribution_hours is None
    assert result.within_category_contribution_hours is None
    assert all(row.mix_contribution_hours is None for row in result.rows)

def test_missing_classifications_do_not_silently_change_population():
    source = dataset([(1, 10, "Routine", 1), (1, 30, None, 1),
                      (2, 10, "Routine", 1), (2, 50, None, 1)])
    result = decompose_case_mix(source, JAN, FEB)
    assert result.status == "incomplete"
    assert result.observed_mean_change_hours == 10
    assert result.categories.baseline.excluded_classification_count == 1
    assert result.rows[0].baseline_share == 0.5
    assert result.mix_contribution_hours is None
    assert "UNCLASSIFIED_REQUESTS_DECOMPOSITION_UNAVAILABLE" in result.warnings

def test_all_classifications_missing_returns_incomplete():
    result = decompose_case_mix(dataset([(1, 10, None, 1), (2, 20, None, 1)]), JAN, FEB)
    assert result.status == "incomplete"
    assert result.categories is None and result.rows == ()
    assert result.observed_mean_change_hours == 10
    assert "NO_CATEGORY_EVIDENCE" in result.warnings

@pytest.mark.parametrize("first,second", [(JAN, FEB), (FEB, MAR), (JAN, MAR)])
def test_empty_periods_are_incomplete(first, second):
    result = decompose_case_mix(dataset([(1, 10, "Routine", 1)]), first, second)
    assert result.status == "incomplete"
    assert result.observed_mean_change_hours is None
    assert result.mix_contribution_hours is None
    assert result.reconciliation_residual_hours is None

def test_small_group_warning_is_preserved():
    result = decompose_case_mix(dataset([(1, 10, "Routine", 1), (2, 20, "Routine", 1)]), JAN, FEB)
    assert result.status == "complete"
    assert "TINY_PERIOD_GROUP" in result.warnings

def test_overlapping_periods_rejected():
    with pytest.raises(ValueError):
        decompose_case_mix(case_mix(), JAN, JAN)

def test_stable_identity_and_json_export():
    source = case_mix()
    first = decompose_case_mix(source, JAN, FEB)
    assert first.result_id == decompose_case_mix(source, JAN, FEB).result_id
    payload = first.model_dump_json()
    assert "REQ-0" not in payload and "NaN" not in payload
    assert json.loads(payload)["method"] == "symmetric-case-mix-v1"
    assert first.dataset_id == first.overall.dataset_id

def test_incomplete_contract_rejects_fabricated_attribution():
    result = decompose_case_mix(dataset([(1, 10, "Routine", 1)]), JAN, FEB)
    payload = result.model_dump()
    payload["mix_contribution_hours"] = 0
    with pytest.raises(ValidationError):
        DecompositionResult.model_validate(payload)

def test_complete_contract_rejects_tampered_totals():
    payload = decompose_case_mix(case_mix(), JAN, FEB).model_dump()
    payload["mix_contribution_hours"] = 0
    payload["within_category_contribution_hours"] = 12
    with pytest.raises(ValidationError):
        DecompositionResult.model_validate(payload)

@pytest.mark.parametrize("factor", [0.1, 0.5, 2, 10])
def test_scaled_oracles(factor):
    source = dataset([(1, 10 * factor, "Routine", 8), (1, 30 * factor, "Complex", 2),
                      (2, 12 * factor, "Routine", 2), (2, 36 * factor, "Complex", 8)])
    assert_totals(decompose_case_mix(source, JAN, FEB), 17.2 * factor, 13.2 * factor, 4 * factor)
