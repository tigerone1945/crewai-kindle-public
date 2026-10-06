import pytest
from pydantic import ValidationError

from shared.schemas import Metric, SalesAnalysisResult


def make(**overrides):
    base = dict(
        question="地域別の売上を分析してください。", analysis_type="region_sales", summary="要約",
        metrics=[Metric(label="東北の売上", value=8439765, unit="円")],
        insights=["a", "b"], recommendations=["c"],
    )
    return SalesAnalysisResult(**{**base, **overrides})


def test_valid_result():
    assert make().analysis_type == "region_sales"


def test_analysis_type_is_restricted_to_known_values():
    with pytest.raises(ValidationError):
        make(analysis_type="regional_sales_analysis")


def test_metrics_required_for_supported_analysis():
    with pytest.raises(ValidationError, match="metrics"):
        make(metrics=[])


@pytest.mark.parametrize("insights", [["a"], ["a", "b", "c", "d", "e"]])
def test_insights_count_is_bounded(insights):
    with pytest.raises(ValidationError, match="insights"):
        make(insights=insights)


def test_recommendations_count_is_bounded():
    with pytest.raises(ValidationError, match="recommendations"):
        make(recommendations=[])


def test_unsupported_requires_empty_metrics():
    ok = make(analysis_type="unsupported", metrics=[], insights=[], recommendations=[])
    assert ok.metrics == []
    with pytest.raises(ValidationError, match="unsupported"):
        make(analysis_type="unsupported")


def test_unit_is_restricted():
    with pytest.raises(ValidationError):
        Metric(label="x", value=1, unit="ドル")


def test_model_dump_round_trip():
    r = make()
    assert SalesAnalysisResult.model_validate(r.model_dump()) == r
