"""売上データの PostgreSQL 版（第15章）。SQLite 版と同じ結果を返し、DB の制約・トランザクションが働くことを確かめる。"""
import pandas as pd
import psycopg
import pytest

import shared.sales_db as sales_db
from shared.join_policy import DIMENSIONS, JoinValidationError, JoinEdge, require_unique_target
from shared.postgres import Database, DatabaseUnavailable
from shared.postgres_sales import PostgresSalesRepository, import_csv_postgres, import_dataframe_postgres
from shared.sales_data import load_sales
from shared.sales_db import DataImportError, SchemaVersionError, SalesRepository, import_csv
from tests.postgres.conftest import URL


@pytest.fixture
def sqlite_repo(tmp_path):
    import_csv(db_path=tmp_path / "s.db")
    return SalesRepository(tmp_path / "s.db")


# --- SQLite 版と同じ結果（Repository の背後を入れ替えても、Tool の出力は変わらない）---

def test_summary_and_data_quality_equal_the_sqlite_implementation(pg_sales, sqlite_repo):
    assert pg_sales.summary() == sqlite_repo.summary()
    assert pg_sales.data_quality() == sqlite_repo.data_quality() | {"imported_at": pg_sales.data_quality()["imported_at"]}


@pytest.mark.parametrize("dimension", list(DIMENSIONS))
def test_every_aggregation_equals_the_sqlite_implementation(pg_sales, sqlite_repo, dimension):
    assert pg_sales.sales_by(dimension) == sqlite_repo.sales_by(dimension)


def test_preview_order_and_dataframe_equal_the_sqlite_implementation(pg_sales, sqlite_repo):
    assert pg_sales.preview("category", 5) == sqlite_repo.preview("category", 5)
    assert pg_sales.get_order("O0001") == sqlite_repo.get_order("O0001")
    assert pg_sales.get_order("O9999") is None
    left = pg_sales.to_dataframe()
    right = sqlite_repo.to_dataframe().sort_values("order_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)


def test_business_rule_values_hold_on_postgresql(pg_sales):
    assert pg_sales.summary() == {"order_count": 300, "total_sales": 28298695, "cancelled_count": 14, "review_count": 37}
    assert pg_sales.sales_by("region")[0] == {"region": "東北", "sales": 8439765, "share_pct": 29.8, "cumulative_share_pct": 29.8, "cumulative_sales": 8439765}


def test_get_order_is_parameterized(pg_sales):
    assert pg_sales.get_order("O0001'; DROP TABLE orders; --") is None
    assert pg_sales.summary()["order_count"] == 300


# --- 制約・最小権限 ---

def test_the_repository_connection_is_read_only(pg_sales, pg_schema):
    reader = Database(URL, schema=pg_schema, read_only=True)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with reader.connection() as conn:
            conn.execute("DELETE FROM orders")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with reader.connection() as conn:
            conn.execute("DROP TABLE orders")
    reader.close()
    assert pg_sales.summary()["order_count"] == 300


def test_database_constraints_protect_the_business_rules(pg_sales, pg_db):
    with pytest.raises(psycopg.errors.CheckViolation):  # BR-02: キャンセルでない注文の sales_amount が 0
        with pg_db.connection() as conn:
            conn.execute("UPDATE orders SET sales_amount = 0 WHERE status = '完了' AND order_id = (SELECT MIN(order_id) FROM orders WHERE status = '完了')")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with pg_db.connection() as conn:
            conn.execute("UPDATE orders SET customer_id = 'C9999' WHERE order_id = 'O0001'")
    assert pg_sales.summary()["total_sales"] == 28298695  # どちらも、ロールバックされた


def test_schema_version_mismatch_is_reported_and_not_repaired_automatically(pg_db, pg_schema):
    with pytest.raises(SchemaVersionError):  # 取り込み前（表がない）
        PostgresSalesRepository(Database(URL, schema=pg_schema, read_only=True))
    import_csv_postgres(pg_db)
    with pg_db.connection() as conn:
        conn.execute("UPDATE schema_version SET version = 1")
    with pytest.raises(SchemaVersionError, match="version 1"):
        PostgresSalesRepository(Database(URL, schema=pg_schema, read_only=True))


def test_join_target_uniqueness_is_checked_in_postgresql(pg_sales, pg_db, pg_schema):
    pg_db_conn = pg_db
    with pg_db_conn.connection() as conn:
        conn.execute("CREATE TABLE dup_products (product_id TEXT, category TEXT)")  # 一意でない結合先
        conn.execute("INSERT INTO dup_products VALUES ('P001', 'IT機器'), ('P001', '周辺機器')")
        assert PostgresSalesRepository._is_unique(conn, JoinEdge("orders", "product_id", "products", "product_id")) is True
        assert PostgresSalesRepository._is_unique(conn, JoinEdge("orders", "product_id", "dup_products", "product_id")) is False
        with pytest.raises(JoinValidationError, match="Many-to-Many"):
            require_unique_target(False, JoinEdge("orders", "product_id", "dup_products", "product_id"))


# --- 取り込み: トランザクション・同時実行 ---

def test_invalid_data_is_rejected_before_touching_the_database(pg_sales, pg_db):
    df = load_sales().copy()
    df.loc[0, "region"] = "火星"
    with pytest.raises(DataImportError):
        import_dataframe_postgres(df, pg_db)
    assert pg_sales.summary()["order_count"] == 300


def test_a_failed_import_rolls_back_everything_including_ddl(pg_sales, pg_db, monkeypatch):
    """PostgreSQL は DDL もトランザクションに入る。取り込みの途中で失敗しても、元のテーブルとデータが残る。"""
    # 検査をすり抜けた不整合が、DB の型の検査で、途中で失敗する場合を再現する（存在しない日付）
    def failing():
        df = load_sales().copy()
        monkeypatch.setattr("shared.postgres_sales.validate_frame", lambda frame: [])
        df.loc[5, "order_date"] = "2026-13-45"
        import_dataframe_postgres(df, pg_db)

    with pytest.raises(psycopg.errors.DatetimeFieldOverflow):
        failing()
    assert pg_sales.summary()["order_count"] == 300 and pg_sales.sales_by("region")[0]["region"] == "東北"


def test_concurrent_imports_are_serialized_and_end_consistent(pg_sales, pg_db):
    import threading
    errors = []

    def run():
        try:
            import_csv_postgres(pg_db)
        except Exception as e:  # pragma: no cover - 失敗したら、下で検出する
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and pg_sales.summary()["order_count"] == 300


def test_readers_see_complete_data_while_an_import_runs(pg_sales, pg_db):
    import threading
    stop, counts = threading.Event(), []

    def reader():
        while not stop.is_set():
            counts.append(pg_sales.summary()["order_count"])

    t = threading.Thread(target=reader)
    t.start()
    for _ in range(3):
        import_csv_postgres(pg_db)
    stop.set()
    t.join()
    assert counts and set(counts) == {300}  # 取り込み中も、空や途中の状態は見えない


# --- 接続 ---

def test_connection_failure_does_not_leak_the_password():
    db = Database("postgresql://app:s3cretPassw0rd@127.0.0.1:1/none")
    with pytest.raises(DatabaseUnavailable) as e:
        with db.connection():
            pass
    assert "s3cretPassw0rd" not in str(e.value) and "127.0.0.1" not in str(e.value)


def test_schema_name_is_validated():
    with pytest.raises(ValueError):
        Database(URL, schema="x; DROP SCHEMA public")


def test_pool_serves_more_threads_than_connections(pg_sales):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(32) as pool:
        results = list(pool.map(lambda _: pg_sales.summary()["order_count"], range(200)))
    assert set(results) == {300}  # 接続は最大 max_size。待たされても、失敗しない


def test_connection_state_does_not_leak_between_borrowers(pg_sales, pg_db):
    """プールの接続に、行の形式などの状態を残さない（get_order のあとの集計が壊れた不具合の再発防止）。"""
    for _ in range(10):
        assert pg_sales.get_order("O0001")["order_id"] == "O0001"
        assert pg_sales.summary()["order_count"] == 300


def test_default_repository_follows_database_url(pg_sales, monkeypatch):
    """DATABASE_URL があれば PostgreSQL、なければ SQLite。切り替えの入口は、ここ1か所。"""
    assert isinstance(SalesRepository.default(), PostgresSalesRepository)
    monkeypatch.delenv("DATABASE_URL")
    assert isinstance(SalesRepository.default(), SalesRepository)
