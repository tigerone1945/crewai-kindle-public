"""売上DB（第9章）のテスト。CSVからの取り込み、目的限定のクエリ、Tool Interface の維持を確かめる。"""
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

import shared.sales_db as sales_db
from shared.sales_data import CSV_PATH, load_sales
from shared.sales_db import DataImportError, SalesRepository, import_csv, import_dataframe, validate_frame
from shared.sales_tools import get_sales_by_category, get_sales_by_region, get_sales_summary
from tests.helpers import ROOT


@pytest.fixture(scope="module")
def repo():
    return SalesRepository.default()


def rows(n=3, **overrides):
    """CSV の先頭 n 行（有効なデータ）。overrides で列を上書きして、不正なデータを作る。"""
    df = load_sales().head(n).copy()
    for column, value in overrides.items():
        df[column] = value
    return df


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "sales.db"


# --- Tool Interface の維持 ---

def test_tool_outputs_are_identical_to_the_csv_implementation():
    """移行前（CSV + pandas）の Tool の出力を保存したもの（tests/golden）と、一字一句同じ。"""
    golden = json.loads((ROOT / "tests" / "golden" / "sales_tools_csv.json").read_text(encoding="utf-8"))
    actual = {t.name: t.run() for t in (get_sales_summary, get_sales_by_category, get_sales_by_region)}
    assert actual == golden


# --- 集計 ---

def test_summary_matches_the_business_rules_expected_values(repo):
    s = repo.summary()
    assert s == {"order_count": 300, "total_sales": 28298695, "cancelled_count": 14, "review_count": 37}


def test_br01_total_uses_sales_amount_not_total_amount(repo):
    assert repo.summary()["total_sales"] != int(load_sales()["total_amount"].sum())


def test_sales_by_region_order_and_values(repo):
    result = repo.sales_by("region")
    assert [r["region"] for r in result] == ["東北", "関西", "九州", "関東", "中部"]
    assert result[0] == {"region": "東北", "sales": 8439765, "share_pct": 29.8}
    assert sum(r["share_pct"] for r in result) == pytest.approx(100, abs=0.2)


def test_sales_by_category_values(repo):
    assert {r["category"]: r["sales"] for r in repo.sales_by("category")} == {
        "IT機器": 11651680, "オフィス家具": 11329340, "周辺機器": 2879995, "ストレージ": 2437680}


def test_sql_aggregation_equals_pandas_aggregation(repo):
    """SQL の集計と、全件を読み込んだ pandas の集計が一致する（移行で数値が変わっていない）。"""
    df = load_sales()
    expected = df.groupby("region")["sales_amount"].sum().to_dict()
    assert {r["region"]: r["sales"] for r in repo.sales_by("region")} == expected


def test_empty_database_gives_zero_and_no_groups(tmp_db):
    import_dataframe(rows(0), tmp_db)
    empty = SalesRepository(tmp_db)
    assert empty.summary() == {"order_count": 0, "total_sales": 0, "cancelled_count": 0, "review_count": 0}
    assert empty.sales_by("region") == []


def test_to_dataframe_keeps_csv_order_and_numeric_types(repo):
    df = repo.to_dataframe()
    assert list(df["order_id"][:3]) == list(load_sales()["order_id"][:3]) and len(df) == 300
    assert pd.api.types.is_integer_dtype(df["sales_amount"])


# --- Parameterized Query / 最小権限 ---

def test_get_order_returns_the_row(repo):
    order = repo.get_order("O0001")
    assert order["sales_amount"] == 264600 and order["review_flag"] == "通常"
    assert repo.get_order("O9999") is None


@pytest.mark.parametrize("evil", ["O0001' OR '1'='1", "' OR 1=1 --", "O0001'; DROP TABLE sales_orders; --"])
def test_order_id_is_a_parameter_not_part_of_the_sql(repo, evil):
    assert repo.get_order(evil) is None
    assert repo.summary()["order_count"] == 300  # テーブルは無事


@pytest.mark.parametrize("column", ["customer_name", "region; DROP TABLE sales_orders", "", "REGION", "sales_amount"])
def test_group_column_must_be_on_the_allowlist(repo, column):
    with pytest.raises(ValueError, match="集計できない列"):
        repo.sales_by(column)
    assert repo.summary()["order_count"] == 300


@pytest.mark.parametrize("statement", [
    "INSERT INTO sales_orders (order_id) VALUES ('X')", "DELETE FROM sales_orders", "DROP TABLE sales_orders",
    "UPDATE sales_orders SET sales_amount = 0"])
def test_repository_connection_is_read_only(repo, statement):
    with repo._connect() as conn, pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute(statement)
    assert repo.summary()["order_count"] == 300


def test_missing_database_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="import_csv"):
        SalesRepository(tmp_path / "nope.db")


def test_default_imports_from_csv_when_the_database_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(sales_db, "DB_PATH", tmp_path / "fresh.db")
    assert SalesRepository.default().summary()["order_count"] == 300


# --- 取り込み ---

def test_import_is_idempotent(tmp_db):
    assert import_csv(db_path=tmp_db) == 300
    assert import_csv(db_path=tmp_db) == 300
    assert SalesRepository(tmp_db).summary()["order_count"] == 300  # 二重に増えない


def test_import_records_the_source_hash(tmp_db):
    import_csv(db_path=tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        digest, count = conn.execute("SELECT source_sha256, row_count FROM import_meta").fetchone()
    assert digest == hashlib.sha256(CSV_PATH.read_bytes()).hexdigest() and count == 300


@pytest.mark.parametrize("name,broken,message", [
    ("missing column", lambda df: df.drop(columns=["region"]), "列が想定と違います"),
    ("extra column", lambda df: df.assign(extra=1), "列が想定と違います"),
    ("duplicate id", lambda df: df.assign(order_id="O0001"), "重複"),
    ("null", lambda df: df.assign(region=None), "空の値"),
    ("bad enum", lambda df: df.assign(region="北海道"), "想定外の値"),
    ("bad date", lambda df: df.assign(order_date="2026/01/01"), "YYYY-MM-DD"),
    ("cancelled with sales", lambda df: df.assign(status="キャンセル"), "BR-02"),
    ("completed without sales", lambda df: df.assign(status="完了", sales_amount=0), "sales_amount が 0"),
])
def test_invalid_csv_is_rejected_with_a_reason(tmp_db, name, broken, message):
    with pytest.raises(DataImportError, match=message):
        import_dataframe(broken(rows()), tmp_db)
    assert not tmp_db.exists()  # 失敗したら何も作らない


def test_failed_import_keeps_the_existing_data(tmp_db):
    import_csv(db_path=tmp_db)
    with pytest.raises(DataImportError):
        import_dataframe(rows(region="北海道"), tmp_db)
    assert SalesRepository(tmp_db).summary()["order_count"] == 300


def test_database_constraints_protect_br02_even_without_the_importer(tmp_db):
    """取り込みの検査をすり抜けて直接書いても、DB の制約が BR-02（キャンセルは売上0）を守る。"""
    import_csv(db_path=tmp_db)
    with sqlite3.connect(tmp_db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE sales_orders SET sales_amount = 100 WHERE status = 'キャンセル'")


def test_validate_frame_accepts_the_real_csv():
    assert validate_frame(load_sales()) == []
