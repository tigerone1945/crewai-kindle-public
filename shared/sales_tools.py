"""全章共通: 売上集計をCrewAI Toolとして公開する（第2章で導入）。

Toolは「目的限定」にする。任意のPythonやSQLを実行するToolは作らない（BR-04）。
Toolは集計関数を呼んで整形するだけで、計算ロジックは shared/sales_analytics.py にある。
"""
from crewai.tools import tool

from shared.sales_analytics import sales_by, summarize_sales
from shared.sales_data import load_sales


@tool("get_sales_summary")
def get_sales_summary() -> str:
    """売上データ全体の基本集計（総レコード数・売上合計・キャンセル件数・要レビュー件数）を返す。
    売上金額にはsales_amountを使用する。"""
    s = summarize_sales(load_sales())
    return (
        f"総レコード数: {s['order_count']}\n"
        f"売上合計: {s['total_sales']:,}円\n"
        f"キャンセル件数: {s['cancelled_count']}\n"
        f"要レビュー件数: {s['review_count']}"
    )


def _format(rows: list[dict], column: str) -> str:
    return "\n".join(f"{r[column]}: {r['sales']:,}円（構成比 {r['share_pct']}%）" for r in rows)


@tool("get_sales_by_category")
def get_sales_by_category() -> str:
    """商品カテゴリ別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(sales_by(load_sales(), "category"), "category")


@tool("get_sales_by_region")
def get_sales_by_region() -> str:
    """地域別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(sales_by(load_sales(), "region"), "region")


SALES_TOOLS = [get_sales_summary, get_sales_by_category, get_sales_by_region]
