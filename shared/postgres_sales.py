"""売上データベースの PostgreSQL 版（第15章）。SQLite 版（shared/sales_db.py）と、同じ入口・同じ結果を返す。

違うのは、実装だけ（Repository の背後を入れ替える）。
- SQL の方言: 件数は `COUNT(*) FILTER (WHERE ...)`、プレースホルダは `%s`、一意の確認は pg_index。
- 型: 日付は DATE、金額は INTEGER、割引率は DOUBLE PRECISION。
- 取り込み: PostgreSQL は DDL もトランザクションに入る。DROP / CREATE / INSERT を1回のトランザクションで行い、失敗すれば元のまま。
  取り込みの間、参照側は、取り込み前のデータを読む（途中の状態は見えない）。同時の取り込みは、アドバイザリロックで直列にする。
- 参照: 読み取り専用のトランザクション。書き込みは DB が拒否する（BR-04）。
- スキーマの版: schema_version 表。想定と違えば SchemaVersionError（自動では作り直さない）。
"""
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from psycopg.rows import dict_row

from shared.join_policy import (FACT_TABLE, JoinEdge, aggregate_sql, plan_for, preview_sql, require_unique_target,
                                validate_totals)
from shared.postgres import Database, get_database
from shared.sales_data import CSV_PATH, load_sales
from shared.sales_db import (CATEGORIES, COLUMNS, CUSTOMER_TYPES, REGIONS, REVIEW_FLAGS, SCHEMA_VERSION, STATUSES,
                             DataImportError, SchemaVersionError, shape_data_quality, shape_sales_by, validate_frame)

logger = logging.getLogger(__name__)

IMPORT_LOCK_KEY = 15_001  # 取り込みを直列にするためのアドバイザリロックの番号


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)  # 固定の定数だけを埋め込む（利用者の入力は入らない）


_SELECT_ALL = (
    "SELECT o.order_id, o.order_date, o.customer_id, c.customer_name, c.customer_type, o.product_id, p.product_name, "
    "p.category, o.unit_price, o.quantity, o.discount_rate, o.subtotal, o.total_amount, o.sales_amount, "
    "o.region, o.salesperson, o.status, o.review_flag "
    "FROM orders o JOIN customers c ON c.customer_id = o.customer_id JOIN products p ON p.product_id = o.product_id"
)

_SCHEMA = f"""
CREATE TABLE customers (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ({_in(CUSTOMER_TYPES)}))
);
CREATE TABLE products (
    product_id   TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ({_in(CATEGORIES)}))
);
CREATE TABLE orders (
    order_id      TEXT PRIMARY KEY,
    order_date    DATE NOT NULL,
    customer_id   TEXT NOT NULL REFERENCES customers (customer_id),
    product_id    TEXT NOT NULL REFERENCES products (product_id),
    unit_price    INTEGER NOT NULL CHECK (unit_price >= 0),
    quantity      INTEGER NOT NULL CHECK (quantity >= 0),
    discount_rate DOUBLE PRECISION NOT NULL CHECK (discount_rate BETWEEN 0 AND 1),
    subtotal      INTEGER NOT NULL CHECK (subtotal >= 0),
    total_amount  INTEGER NOT NULL CHECK (total_amount >= 0),
    sales_amount  INTEGER NOT NULL CHECK (sales_amount >= 0),
    region        TEXT NOT NULL CHECK (region IN ({_in(REGIONS)})),
    salesperson   TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ({_in(STATUSES)})),
    review_flag   TEXT NOT NULL CHECK (review_flag IN ({_in(REVIEW_FLAGS)})),
    -- BR-02: キャンセル注文は実売上に含めない。DB の制約でも守る
    CHECK ((status = 'キャンセル') = (sales_amount = 0))
);
CREATE INDEX idx_orders_region ON orders (region);
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_product ON orders (product_id);
CREATE VIEW sales_orders AS {_SELECT_ALL};
CREATE TABLE import_meta (source_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL, imported_at TIMESTAMPTZ NOT NULL);
CREATE TABLE schema_version (version INTEGER NOT NULL);
"""


def _py(value):
    return value.item() if hasattr(value, "item") else value


def import_dataframe_postgres(df: pd.DataFrame, db: Database, source_sha256: str = "") -> int:
    """検査したうえで、1回のトランザクションでテーブルを作り直す。途中で失敗したら、元のデータは変わらない。"""
    problems = validate_frame(df)
    if problems:
        raise DataImportError("取り込めません: " + " / ".join(problems))
    customers = [tuple(_py(v) for v in r) for r in df[["customer_id", "customer_name", "customer_type"]].drop_duplicates("customer_id").itertuples(index=False, name=None)]
    products = [tuple(_py(v) for v in r) for r in df[["product_id", "product_name", "category"]].drop_duplicates("product_id").itertuples(index=False, name=None)]
    order_columns = [c for c in COLUMNS if c not in ("customer_name", "customer_type", "product_name", "category")]
    orders = [tuple(_py(v) for v in r) for r in df[order_columns].itertuples(index=False, name=None)]
    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (IMPORT_LOCK_KEY,))  # 同時の取り込みを直列にする（トランザクションの終わりで解放）
        conn.execute("DROP VIEW IF EXISTS sales_orders")
        for table in ("orders", "customers", "products", "import_meta", "schema_version"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")  # 固定の名前だけ
        conn.execute(_SCHEMA)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO customers VALUES (%s, %s, %s)", customers)
            cur.executemany("INSERT INTO products VALUES (%s, %s, %s)", products)
            cur.executemany(f"INSERT INTO orders ({', '.join(order_columns)}) VALUES ({', '.join(['%s'] * len(order_columns))})", orders)
            cur.execute("INSERT INTO import_meta VALUES (%s, %s, %s)", (source_sha256, len(orders), datetime.now(timezone.utc)))
            cur.execute("INSERT INTO schema_version VALUES (%s)", (SCHEMA_VERSION,))
    return len(orders)


def import_csv_postgres(db: Database, csv_path: Path = CSV_PATH) -> int:
    digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest() if Path(csv_path).exists() else ""
    return import_dataframe_postgres(load_sales(csv_path), db, digest)


class PostgresSalesRepository:
    """売上の参照（PostgreSQL）。読み取り専用のトランザクションで、目的ごとに固定した SQL だけを実行する。"""

    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_database(read_only=True)
        with self._db.connection() as conn:
            exists = conn.execute("SELECT to_regclass('schema_version') IS NOT NULL").fetchone()[0]
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0] if exists else None
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"データベースのスキーマが、想定と違います（version {version}、必要なのは {SCHEMA_VERSION}）。"
                "CSV から取り込むには `uv run python chapters/ch15-docker-postgresql/import_to_postgres.py` を実行してください"
            )

    def summary(self) -> dict:
        with self._db.connection() as conn:
            count, total, cancelled, review = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(sales_amount), 0), COUNT(*) FILTER (WHERE status = 'キャンセル'), "
                "COUNT(*) FILTER (WHERE review_flag = '要レビュー') FROM orders"
            ).fetchone()
        return {"order_count": count, "total_sales": int(total), "cancelled_count": int(cancelled), "review_count": int(review)}

    def data_quality(self) -> dict:
        with self._db.connection() as conn:
            count, start, end, cancelled, review, over_total, cancelled_with_sales = conn.execute(
                "SELECT COUNT(*), MIN(order_date), MAX(order_date), COUNT(*) FILTER (WHERE status = 'キャンセル'), "
                "COUNT(*) FILTER (WHERE review_flag = '要レビュー'), COUNT(*) FILTER (WHERE sales_amount > total_amount), "
                "COUNT(*) FILTER (WHERE status = 'キャンセル' AND sales_amount <> 0) FROM orders"
            ).fetchone()
            orphans = conn.execute(
                "SELECT COUNT(*) FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id "
                "LEFT JOIN products p ON p.product_id = o.product_id WHERE c.customer_id IS NULL OR p.product_id IS NULL"
            ).fetchone()[0]
            row = conn.execute("SELECT imported_at FROM import_meta").fetchone()
        imported_at = row[0].astimezone(timezone.utc).isoformat(timespec="seconds") if row else ""
        return shape_data_quality(count, start.isoformat() if start else None, end.isoformat() if end else None,
                                  cancelled, review, over_total, cancelled_with_sales, orphans, imported_at)

    @staticmethod
    def _is_unique(conn, edge: JoinEdge) -> bool:
        """結合先のキー（単独の列）が、主キーまたは一意制約・一意索引で守られているか。"""
        return bool(conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = current_schema() AND t.relname = %s AND i.indisunique AND i.indnatts = 1 "
            "AND (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = t.oid AND a.attnum = i.indkey[0]) = %s)",
            (edge.right_table, edge.right_key)).fetchone()[0])

    def sales_by(self, dimension: str) -> list[dict]:
        plan = plan_for(dimension)  # 許可されていない軸・結合は、ここで JoinNotAllowed（ValueError）
        with self._db.connection() as conn:
            if plan.edge:
                require_unique_target(self._is_unique(conn, plan.edge), plan.edge)  # 静的な検査
            rows = conn.execute(aggregate_sql(plan)).fetchall()
            fact_sales, fact_orders = conn.execute(f"SELECT COALESCE(SUM(sales_amount), 0), COUNT(*) FROM {FACT_TABLE}").fetchone()
        validate_totals(rows, int(fact_sales), int(fact_orders))  # 実行時の検査
        return shape_sales_by(dimension, rows)

    def preview(self, dimension: str, limit: int = 5) -> list[dict]:
        if not 1 <= limit <= 20:
            raise ValueError("limit は1〜20にしてください")
        plan = plan_for(dimension)
        with self._db.connection() as conn:
            if plan.edge:
                require_unique_target(self._is_unique(conn, plan.edge), plan.edge)
            rows = conn.execute(preview_sql(plan).replace("?", "%s"), (limit,)).fetchall()  # SQL は固定の文字列。? はプレースホルダだけ
        return [{"order_id": o, dimension: k, "sales_amount": int(a)} for o, k, a in rows]

    def get_order(self, order_id: str) -> dict | None:
        with self._db.connection() as conn:
            # 行の形式は、接続ではなくカーソルに指定する。接続に設定すると、プールへ戻った接続に残り、次の借り手の結果が壊れる
            row = conn.cursor(row_factory=dict_row).execute(f"{_SELECT_ALL} WHERE o.order_id = %s", (order_id,)).fetchone()
        if row is None:
            return None
        row["order_date"] = row["order_date"].isoformat()  # SQLite 版と同じ、文字列の日付
        return row

    def to_dataframe(self) -> pd.DataFrame:
        with self._db.connection() as conn:
            rows = conn.execute(f"{_SELECT_ALL} ORDER BY o.order_id").fetchall()
        df = pd.DataFrame(rows, columns=list(COLUMNS))
        df["order_date"] = df["order_date"].map(lambda d: d.isoformat())
        return df
