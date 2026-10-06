import pytest

from shared.sales_db import SalesRepository
from shared.sales_validation import find_ungrounded_metrics, find_ungrounded_numbers_in_text
from shared.schemas import Metric, SalesAnalysisResult


@pytest.fixture(scope="module")
def df():  # 名前は変えない（各テストの引数）。中身は Repository
    return SalesRepository.default()


def result_with(*metrics):
    return SalesAnalysisResult(
        question="q", analysis_type="region_sales", summary="s",
        metrics=list(metrics), insights=["a", "b"], recommendations=["c"],
    )


def test_real_values_pass(df):
    r = result_with(
        Metric(label="東北の売上", value=8439765, unit="円"),
        Metric(label="東北の構成比", value=29.8, unit="%"),
        Metric(label="キャンセル件数", value=14, unit="件"),
        Metric(label="売上合計", value=28298695, unit="円"),
    )
    assert find_ungrounded_metrics(r, df) == []


def test_wrong_total_amount_is_detected(df):
    r = result_with(Metric(label="売上合計", value=30500770, unit="円"))  # total_amount の合計（BR-01違反）
    problems = find_ungrounded_metrics(r, df)
    assert len(problems) == 1 and "30,500,770円" in problems[0]


def test_invented_number_is_detected(df):
    assert find_ungrounded_metrics(result_with(Metric(label="x", value=12345, unit="円")), df)


def test_unit_mismatch_is_detected(df):
    # 14は件数としては正しいが、金額としては存在しない
    assert find_ungrounded_metrics(result_with(Metric(label="x", value=14, unit="円")), df)


def test_percent_tolerates_rounding_only(df):
    assert find_ungrounded_metrics(result_with(Metric(label="x", value=29.82, unit="%")), df) == []
    assert find_ungrounded_metrics(result_with(Metric(label="x", value=35.0, unit="%")), df)


def text_result(summary="s", insights=("a", "b")):
    return SalesAnalysisResult(
        question="q", analysis_type="region_sales", summary=summary,
        metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=list(insights), recommendations=["c"],
    )


def test_text_numbers_that_match_data_pass(df):
    r = text_result(insights=("東北は8,439,765円で全体の29.8%を占める", "売上合計は28,298,695円"))
    assert find_ungrounded_numbers_in_text(r, df) == []


def test_text_percentage_computed_by_llm_is_detected(df):
    # 実データの構成比にない比率を文章に混ぜた場合（Spikeで観測した事例: LLMが自分で比率を計算）
    problems = find_ungrounded_numbers_in_text(text_result(insights=("東北は全体の31.5%を占める", "b")), df)
    assert len(problems) == 1 and "31.5%" in problems[0]


def test_text_amount_not_in_data_is_detected(df):
    assert find_ungrounded_numbers_in_text(text_result(summary="売上は30,500,770円です"), df)


def test_years_and_counts_without_separators_are_not_flagged(df):
    assert find_ungrounded_numbers_in_text(text_result(insights=("2026年の売上", "3地域が対象")), df) == []


def test_values_from_joined_dimensions_are_legitimate(df):
    """結合を伴う軸（顧客種別・商品）の数値も、正しい値として認められる。"""
    r = result_with(
        Metric(label="法人の売上", value=24256195, unit="円"),
        Metric(label="法人の構成比", value=85.7, unit="%"),
        Metric(label="個人の売上", value=4042500, unit="円"),
    )
    assert find_ungrounded_metrics(r, df) == []


def test_a_sum_across_customer_types_that_is_not_a_tool_value_is_detected(df):
    r = result_with(Metric(label="法人と個人の合算", value=28298696, unit="円"))
    assert find_ungrounded_metrics(r, df)
