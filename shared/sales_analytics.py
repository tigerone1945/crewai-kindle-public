"""売上集計（決定論的な処理）。LLMには計算させず、ここで計算する。

Business Rule: 売上は sales_amount で集計する（BR-01）。キャンセル注文は sales_amount が 0 のため
自動的に実売上へ含まれない（BR-02）。
"""
import pandas as pd

ALLOWED_GROUP_COLUMNS = ("category", "region")


def summarize_sales(df: pd.DataFrame) -> dict:
    return {
        "order_count": len(df),
        "total_sales": int(df["sales_amount"].sum()),
        "cancelled_count": int((df["status"] == "キャンセル").sum()),
        "review_count": int((df["review_flag"] == "要レビュー").sum()),
    }


def sales_by(df: pd.DataFrame, column: str) -> list[dict]:
    """指定列（許可された列のみ）で売上を集計し、売上の大きい順に返す。構成比(%)も計算して返す。"""
    if column not in ALLOWED_GROUP_COLUMNS:
        raise ValueError(f"集計できない列です: {column}（許可: {ALLOWED_GROUP_COLUMNS}）")
    grouped = df.groupby(column)["sales_amount"].sum().sort_values(ascending=False)
    total = int(grouped.sum())
    return [
        {
            column: key,
            "sales": int(value),
            "share_pct": round(int(value) / total * 100, 1) if total else 0.0,
        }
        for key, value in grouped.items()
    ]
