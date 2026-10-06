"""Allowed Join と Join Validation（第10章）: 複数の表を、安全に結合して集計するための境界。

Agent に SQL を書かせない（BR-04）。分析の軸（Dimension）と、結合してよい組み合わせ（Allowed Join）を、
System 側のコードで宣言する（Semantic Boundary）。SQL は、この宣言から組み立てる。入力は SQL に入らない。

結合の危険は、「行が増える・減る」ことで、売上の数値が静かに狂うこと。特に Many-to-Many で、売上が水増しされる。
そこで、検査を二重にする。
- 静的な検査（validate_edge）: 結合先のキーが一意か。一意でなければ、左の1行が右の複数行に対応し、行が増える。
- 実行時の検査（validate_totals）: 結合して集計した合計と件数が、結合前（orders だけ）と一致するか。
Data Governance: 氏名などは Dimension に載せない。載せたものだけが、Agent から見える。
"""
import re
import sqlite3
from dataclasses import dataclass

FACT_TABLE = "orders"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


class JoinNotAllowed(ValueError):
    """許可されていない軸や結合。"""


class JoinValidationError(RuntimeError):
    """結合の結果が、結合前と食い違う（行が増えた・減った）。数値を返してはいけない。"""


@dataclass(frozen=True)
class JoinEdge:
    """結合してよい組み合わせ。左（orders）の1行は、右の高々1行に対応する（Many-to-One）。"""

    left_table: str
    left_key: str
    right_table: str
    right_key: str


@dataclass(frozen=True)
class Dimension:
    """集計の軸。どの表のどの列かを、コードで宣言する。"""

    name: str
    table: str
    column: str


ALLOWED_JOINS = (
    JoinEdge(FACT_TABLE, "customer_id", "customers", "customer_id"),
    JoinEdge(FACT_TABLE, "product_id", "products", "product_id"),
)

# 氏名（customer_name）・ID は、あえて入れない。Agent から見える軸は、ここに載せたものだけ。
DIMENSIONS = {
    d.name: d
    for d in (
        Dimension("region", FACT_TABLE, "region"),
        Dimension("category", "products", "category"),
        Dimension("customer_type", "customers", "customer_type"),
        Dimension("product_name", "products", "product_name"),
    )
}

for _name in [*(n for e in ALLOWED_JOINS for n in (e.left_table, e.left_key, e.right_table, e.right_key)),
              *(n for d in DIMENSIONS.values() for n in (d.table, d.column))]:
    assert _IDENTIFIER.match(_name), f"識別子として不正です: {_name}"  # SQL に入る名前は、この検査を通った定数だけ


@dataclass(frozen=True)
class JoinPlan:
    dimension: Dimension
    edge: JoinEdge | None  # 事実表の列なら結合は不要


def plan_for(dimension_name: str) -> JoinPlan:
    """軸の名前から、結合の計画を作る。許可されていない軸・結合は拒否する。"""
    dimension = DIMENSIONS.get(dimension_name)
    if dimension is None:
        raise JoinNotAllowed(f"集計できない軸です: {dimension_name}（許可: {tuple(DIMENSIONS)}）")
    if dimension.table == FACT_TABLE:
        return JoinPlan(dimension, None)
    for edge in ALLOWED_JOINS:
        if edge.left_table == FACT_TABLE and edge.right_table == dimension.table:
            return JoinPlan(dimension, edge)
    raise JoinNotAllowed(f"{FACT_TABLE} と {dimension.table} の結合は許可されていません")


def aggregate_sql(plan: JoinPlan) -> str:
    """軸ごとの売上と件数を集計する SQL。名前はすべてコードの定数で、入力は入らない。"""
    d, e = plan.dimension, plan.edge
    join = f"JOIN {e.right_table} d ON d.{e.right_key} = o.{e.left_key}" if e else ""
    source = "d" if e else "o"
    return (
        f"SELECT {source}.{d.column} AS key, SUM(o.sales_amount) AS sales, COUNT(*) AS orders "
        f"FROM {FACT_TABLE} o {join} GROUP BY {source}.{d.column} ORDER BY sales DESC, key"
    )


def preview_sql(plan: JoinPlan) -> str:
    """結合の形を確かめるための、少数の行（Preview）。件数は、呼び出し側がパラメータで渡す。"""
    d, e = plan.dimension, plan.edge
    join = f"JOIN {e.right_table} d ON d.{e.right_key} = o.{e.left_key}" if e else ""
    source = "d" if e else "o"
    return (f"SELECT o.order_id, {source}.{d.column} AS key, o.sales_amount FROM {FACT_TABLE} o {join} "
            "ORDER BY o.order_id LIMIT ?")


def _is_unique(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """列（単独）が、主キーまたは一意制約・一意索引で守られているか。"""
    for _, name, unique, *_ in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if unique and [r[2] for r in conn.execute(f"PRAGMA index_info({name})").fetchall()] == [column]:
            return True
    pk = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r[5]]
    return pk == [column]


def validate_edge(conn: sqlite3.Connection, edge: JoinEdge) -> None:
    """静的な検査。結合先のキーが一意でなければ、左の1行が右の複数行に対応し、行が増える（Many-to-Many）。

    許可リスト（ALLOWED_JOINS）への登録とは別の関門。誤って一意でない結合を許可リストに加えても、ここで止まる。
    """
    if not _is_unique(conn, edge.right_table, edge.right_key):
        raise JoinValidationError(
            f"結合先 {edge.right_table}.{edge.right_key} が一意ではありません。結合すると行が増え、売上が水増しされます（Many-to-Many）"
        )


def validate_totals(rows: list[tuple], fact_sales: int, fact_orders: int) -> None:
    """実行時の検査。結合して集計した合計・件数が、結合前（orders だけ）と一致しなければ、数値を返さない。"""
    sales = sum(int(r[1]) for r in rows)
    orders = sum(int(r[2]) for r in rows)
    if sales != fact_sales or orders != fact_orders:
        raise JoinValidationError(
            f"結合の結果が結合前と一致しません（売上 {sales:,} ≠ {fact_sales:,}、件数 {orders} ≠ {fact_orders}）。"
            "行が増えたか、減っています"
        )
