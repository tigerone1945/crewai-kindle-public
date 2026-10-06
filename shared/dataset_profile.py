"""Dataset Profile: CSV全体ではなく、件数・列名・期間だけをAgentへ渡すための要約。"""
import pandas as pd

REQUIRED_COLUMNS = ("order_date",)


def build_dataset_profile(df: pd.DataFrame) -> str:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"必須列がありません: {missing}")
    if df.empty:
        raise ValueError("データが空です")

    return (
        "このCSVは売上・受注データです。\n\n"
        f"レコード数: {len(df)}\n"
        f"列名: {', '.join(df.columns)}\n"
        f"注文日の最小値: {df['order_date'].min()}\n"
        f"注文日の最大値: {df['order_date'].max()}"
    )
