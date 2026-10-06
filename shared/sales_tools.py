"""全章共通: 売上集計をCrewAI Toolとして公開する（第2章で導入）。

Toolは「目的限定」にする。任意のPythonやSQLを実行するToolは作らない（BR-04）。
Toolは Repository（shared/sales_db.py）を呼んで整形するだけ。集計は SQL で行い、Tool の入出力は第2章から変えない（第9章）。
複数の表にまたがる集計も、Tool は軸の名前を渡すだけ。結合は、許可されたものだけが使われる（第10章）。
"""
from crewai.tools import tool

from shared.sales_db import SalesRepository


@tool("get_sales_summary")
def get_sales_summary() -> str:
    """売上データ全体の基本集計（総レコード数・売上合計・キャンセル件数・要レビュー件数）を返す。
    売上金額にはsales_amountを使用する。「売上全体の状況」「全体の売上」を聞かれたときは、このToolだけで答えられる。
    地域・カテゴリ・顧客種別・商品ごとの内訳は含まない。内訳を聞かれたときだけ、それぞれの集計Toolを使う。"""
    s = SalesRepository.default().summary()
    return (
        f"総レコード数: {s['order_count']}\n"
        f"売上合計: {s['total_sales']:,}円\n"
        f"キャンセル件数: {s['cancelled_count']}\n"
        f"要レビュー件数: {s['review_count']}"
    )


def _format(rows: list[dict], column: str) -> str:
    return "\n".join(
        f"{r[column]}: {r['sales']:,}円（構成比 {r['share_pct']}%、上位からの累積 {r['cumulative_sales']:,}円・{r['cumulative_share_pct']}%）" for r in rows
    )


@tool("get_sales_by_category")
def get_sales_by_category() -> str:
    """商品カテゴリ別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(SalesRepository.default().sales_by("category"), "category")


@tool("get_sales_by_region")
def get_sales_by_region() -> str:
    """地域別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(SalesRepository.default().sales_by("region"), "region")


@tool("get_sales_by_customer_type")
def get_sales_by_customer_type() -> str:
    """顧客種別（法人・個人）別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(SalesRepository.default().sales_by("customer_type"), "customer_type")


@tool("get_sales_by_product")
def get_sales_by_product() -> str:
    """商品別の売上と構成比を、売上の大きい順に返す。売上金額にはsales_amountを使用する。"""
    return _format(SalesRepository.default().sales_by("product_name"), "product_name")


SALES_TOOLS = [get_sales_summary, get_sales_by_category, get_sales_by_region, get_sales_by_customer_type, get_sales_by_product]
