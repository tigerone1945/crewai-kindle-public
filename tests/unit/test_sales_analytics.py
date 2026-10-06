import pandas as pd
import pytest

from shared.sales_analytics import sales_by, summarize_sales
from shared.sales_data import load_sales


@pytest.fixture(scope="module")
def df():
    return load_sales()


def test_summary_matches_expected_values(df):
    s = summarize_sales(df)
    assert s == {"order_count": 300, "total_sales": 28298695, "cancelled_count": 14, "review_count": 37}


def test_summary_uses_sales_amount_not_total_amount(df):
    assert summarize_sales(df)["total_sales"] != int(df["total_amount"].sum())  # BR-01


def test_sales_by_region_expected_values_and_order(df):
    rows = sales_by(df, "region")
    assert [(r["region"], r["sales"]) for r in rows] == [
        ("東北", 8439765), ("関西", 8373825), ("九州", 4980410), ("関東", 3268525), ("中部", 3236170),
    ]


def test_sales_by_category_expected_values(df):
    rows = sales_by(df, "category")
    assert {r["category"]: r["sales"] for r in rows} == {
        "IT機器": 11651680, "オフィス家具": 11329340, "周辺機器": 2879995, "ストレージ": 2437680,
    }


def test_share_pct_sums_to_about_100(df):
    assert sum(r["share_pct"] for r in sales_by(df, "region")) == pytest.approx(100, abs=0.2)


def test_cancelled_orders_are_excluded_from_sales():
    df = pd.DataFrame({
        "region": ["東北", "東北"], "status": ["完了", "キャンセル"],
        "review_flag": ["通常", "通常"], "sales_amount": [100, 0], "total_amount": [100, 900],
    })
    assert summarize_sales(df)["total_sales"] == 100  # BR-02
    assert sales_by(df, "region")[0]["sales"] == 100


def test_sales_by_rejects_columns_that_are_not_allowed(df):
    with pytest.raises(ValueError, match="集計できない列"):
        sales_by(df, "customer_name")


def test_sales_by_handles_empty_dataframe():
    empty = pd.DataFrame({"region": [], "sales_amount": []})
    assert sales_by(empty, "region") == []
