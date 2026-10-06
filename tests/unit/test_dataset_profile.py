import pandas as pd
import pytest

from shared.dataset_profile import build_dataset_profile
from shared.sales_data import load_sales


def test_profile_has_count_columns_and_period():
    df = load_sales()
    profile = build_dataset_profile(df)
    assert "レコード数: 300" in profile
    assert "order_id" in profile and "sales_amount" in profile
    assert "2026-01-01" in profile and "2026-09-16" in profile


def test_profile_does_not_leak_aggregates():
    """第1章の原則: 実データの集計値や値の一覧はAgentへ渡さない。"""
    profile = build_dataset_profile(load_sales())
    assert "28,298,695" not in profile and "28298695" not in profile
    for value in ("法人", "個人", "東北", "キャンセル"):
        assert value not in profile


def test_profile_rejects_empty_dataframe():
    with pytest.raises(ValueError, match="空"):
        build_dataset_profile(pd.DataFrame(columns=["order_date"]))


def test_profile_rejects_missing_required_column():
    with pytest.raises(ValueError, match="order_date"):
        build_dataset_profile(pd.DataFrame({"x": [1]}))


def test_load_sales_reports_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="売上CSV"):
        load_sales(tmp_path / "nope.csv")
