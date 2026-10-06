"""Allowed Join と Join Validation（第10章）のテスト。LLMは呼ばない。"""
import sqlite3

import pandas as pd
import pytest

import shared.join_policy as jp
import shared.sales_db as sales_db
from shared.join_policy import (DIMENSIONS, JoinNotAllowed, JoinValidationError, aggregate_sql, plan_for, validate_edge,
                                validate_totals)
from shared.sales_data import load_sales
from shared.sales_db import DataImportError, SalesRepository, import_csv, import_dataframe


@pytest.fixture(scope="module")
def repo():
    return SalesRepository.default()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sales.db"
    import_csv(db_path=path)
    return path


# --- データモデル ---

def test_orders_are_split_into_three_tables(db):
    with sqlite3.connect(db) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "customers", "products")}
        unit_prices = conn.execute("SELECT COUNT(DISTINCT unit_price) FROM orders WHERE product_id = 'P001'").fetchone()[0]
    assert counts == {"orders": 300, "customers": 10, "products": 12}
    assert unit_prices > 1  # 価格は注文時点の値で、商品ごとに変わる。products ではなく orders に置く


def test_compatibility_view_returns_the_original_rows_and_columns(repo):
    original = load_sales()
    view = repo.to_dataframe()
    assert list(view.columns) == list(original.columns)
    pd.testing.assert_frame_equal(view, original, check_dtype=False)


def test_foreign_keys_are_enforced(db):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE orders SET customer_id = 'C999' WHERE order_id = 'O0001'")


def test_inconsistent_customer_attributes_cannot_be_split(tmp_path):
    df = load_sales().copy()
    first = df["customer_id"].iloc[0]
    df.loc[df.index[df["customer_id"] == first][0], "customer_name"] = "別名の会社"
    with pytest.raises(DataImportError, match="customer_id"):
        import_dataframe(df, tmp_path / "x.db")


# --- 軸と許可された結合 ---

@pytest.mark.parametrize("name,table,joined", [("region", "orders", False), ("category", "products", True),
                                               ("customer_type", "customers", True), ("product_name", "products", True)])
def test_plan_for_allowed_dimensions(name, table, joined):
    plan = plan_for(name)
    assert plan.dimension.table == table and (plan.edge is not None) is joined


@pytest.mark.parametrize("name", ["customer_name", "customer_id", "salesperson", "sales_amount", "", "region; DROP TABLE orders", "REGION"])
def test_dimensions_that_are_not_declared_are_refused(name):
    with pytest.raises(JoinNotAllowed):
        plan_for(name)


def test_personal_names_are_not_dimensions():
    """Data Governance: 氏名は、Agent から見える軸に載せない。"""
    assert not any("name" in d.column and d.column != "product_name" for d in DIMENSIONS.values())


def test_a_join_that_is_not_declared_is_refused(monkeypatch):
    monkeypatch.setitem(DIMENSIONS, "x", jp.Dimension("x", "salespeople", "name"))
    with pytest.raises(JoinNotAllowed, match="結合は許可されていません"):
        plan_for("x")


def test_sql_contains_only_declared_names_and_no_input():
    sql = aggregate_sql(plan_for("customer_type"))
    assert "JOIN customers d ON d.customer_id = o.customer_id" in sql and "?" not in sql
    assert "SUM(o.sales_amount)" in sql  # 売上は sales_amount（BR-01）


# --- 集計 ---

def test_sales_by_customer_type_matches_the_expected_values(repo):
    result = repo.sales_by("customer_type")
    assert [(r["customer_type"], r["sales"]) for r in result] == [("法人", 24256195), ("個人", 4042500)]
    assert result[0]["share_pct"] == 85.7


@pytest.mark.parametrize("dimension", list(DIMENSIONS))
def test_every_dimension_sums_to_the_total_without_fan_out(repo, dimension):
    assert sum(r["sales"] for r in repo.sales_by(dimension)) == repo.summary()["total_sales"]


def test_sql_join_equals_pandas_merge(repo):
    df = load_sales()
    expected = df.groupby("customer_type")["sales_amount"].sum().to_dict()
    assert {r["customer_type"]: r["sales"] for r in repo.sales_by("customer_type")} == expected


def test_preview_shows_the_joined_shape(repo):
    rows_ = repo.preview("customer_type", 3)
    assert [r["order_id"] for r in rows_] == ["O0001", "O0002", "O0003"] and rows_[0]["customer_type"] == "法人"


@pytest.mark.parametrize("limit", [0, 21, -1])
def test_preview_limit_is_bounded(repo, limit):
    with pytest.raises(ValueError):
        repo.preview("region", limit)


# --- Join Validation ---

def add_bridge(db):
    """顧客と担当者の対応表。1顧客に複数の担当者がいる（Many-to-Many）。"""
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE customer_salespersons (customer_id TEXT, salesperson TEXT)")
        conn.execute("INSERT INTO customer_salespersons SELECT DISTINCT customer_id, salesperson FROM orders")


def test_many_to_many_join_inflates_sales_and_validation_detects_it(db):
    add_bridge(db)
    with sqlite3.connect(db) as conn:
        true_total = conn.execute("SELECT SUM(sales_amount) FROM orders").fetchone()[0]
        rows_ = conn.execute(
            "SELECT b.salesperson, SUM(o.sales_amount), COUNT(*) FROM orders o "
            "JOIN customer_salespersons b ON b.customer_id = o.customer_id GROUP BY b.salesperson").fetchall()
        inflated = sum(r[1] for r in rows_)
        assert inflated > true_total * 2  # 売上が数倍に水増しされる
        with pytest.raises(JoinValidationError, match="一致しません"):
            validate_totals(rows_, true_total, 300)
        edge = jp.JoinEdge("orders", "customer_id", "customer_salespersons", "customer_id")
        with pytest.raises(JoinValidationError, match="Many-to-Many"):
            validate_edge(conn, edge)


def test_a_wrongly_allowed_many_to_many_edge_is_still_stopped_before_any_number_is_returned(db, monkeypatch):
    add_bridge(db)
    bad = jp.JoinEdge("orders", "customer_id", "customer_salespersons", "customer_id")
    monkeypatch.setattr(jp, "ALLOWED_JOINS", (*jp.ALLOWED_JOINS, bad))
    monkeypatch.setitem(DIMENSIONS, "salesperson_via_bridge", jp.Dimension("salesperson_via_bridge", "customer_salespersons", "salesperson"))
    with pytest.raises(JoinValidationError, match="Many-to-Many"):
        SalesRepository(db).sales_by("salesperson_via_bridge")  # 許可リストに誤って加えても、静的な検査で止まる


def test_dropped_rows_are_detected_too():
    """内部結合で行が減る場合（存在しない顧客を指す注文）も、合計の不一致として検出する。"""
    with pytest.raises(JoinValidationError):
        validate_totals([("法人", 100, 1)], fact_sales=150, fact_orders=2)


def test_validate_edge_accepts_the_declared_edges(db):
    with sqlite3.connect(db) as conn:
        for edge in jp.ALLOWED_JOINS:
            validate_edge(conn, edge)


def test_empty_database_still_validates(tmp_path):
    path = tmp_path / "e.db"
    import_dataframe(load_sales().head(0), path)
    assert SalesRepository(path).sales_by("customer_type") == []
