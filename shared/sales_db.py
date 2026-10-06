"""売上データベース（第9章）: CSV を SQLite へ取り込み、目的限定のクエリだけを公開する。

- 取り込み（import_csv）: CSV を検証して、1回のトランザクションで置き換える。CSV は初期データであり、以後の正本は DB（BR-07）。
- 参照（SalesRepository）: Agent の Tool が使う入口。読み取り専用の接続で、SQL は目的ごとに固定し、値は必ずパラメータで渡す（BR-04）。
  第10章で、1つの表を customers / products / orders に分けた。複数の表にまたがる集計は、許可された結合だけを使う（shared/join_policy.py）。
  互換のため、元の18列を返す VIEW（sales_orders）を残している。
  Tool の入出力は第2章から変えない。変わるのは、集計の実装だけ（Tool Interface を維持する）。
- 売上は sales_amount で集計する（BR-01）。キャンセル注文は sales_amount が 0（BR-02）。この不変条件は取り込み時にも検査する。
"""
import hashlib
import logging
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from shared.join_policy import JoinValidationError, aggregate_sql, plan_for, preview_sql, validate_edge, validate_totals  # noqa: F401
from shared.sales_data import CSV_PATH, ROOT, load_sales

logger = logging.getLogger(__name__)

DB_PATH = ROOT / "data" / "sales.db"
SCHEMA_VERSION = 2  # 1: 単一の表（第9章） / 2: customers・products・orders（第10章）

COLUMNS = (
    "order_id", "order_date", "customer_id", "customer_name", "customer_type", "product_id", "product_name",
    "category", "unit_price", "quantity", "discount_rate", "subtotal", "total_amount", "sales_amount",
    "region", "salesperson", "status", "review_flag",
)
CUSTOMER_TYPES = ("法人", "個人")
CATEGORIES = ("IT機器", "周辺機器", "オフィス家具", "ストレージ")
REGIONS = ("関東", "関西", "中部", "東北", "九州")
STATUSES = ("完了", "処理中", "キャンセル", "保留")
REVIEW_FLAGS = ("通常", "要レビュー")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)  # 固定の定数だけを埋め込む（利用者の入力は入らない）


# 元の18列（CSV と同じ列と順序）を返す。VIEW と、全件・1件の取得で共有する
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
    order_date    TEXT NOT NULL,
    customer_id   TEXT NOT NULL REFERENCES customers (customer_id),
    product_id    TEXT NOT NULL REFERENCES products (product_id),
    unit_price    INTEGER NOT NULL CHECK (unit_price >= 0),  -- 注文時点の価格。商品ごとに変わるため、products には置かない
    quantity      INTEGER NOT NULL CHECK (quantity >= 0),
    discount_rate REAL NOT NULL CHECK (discount_rate BETWEEN 0 AND 1),
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
CREATE TABLE import_meta (source_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL, imported_at TEXT NOT NULL);
"""


class SchemaVersionError(RuntimeError):
    """データベースのスキーマが、このコードの想定と違う。DB は正本なので、自動では作り直さない。"""


class DataImportError(ValueError):
    """CSV が DB の契約（列・型・値・不変条件）を満たさない。"""


def validate_frame(df: pd.DataFrame) -> list[str]:
    """取り込み前の検査。問題の一覧を日本語で返す（空なら問題なし）。DB の制約より前に、原因が分かる形で知らせる。"""
    if list(df.columns) != list(COLUMNS):
        return [f"列が想定と違います。想定: {list(COLUMNS)} / 実際: {list(df.columns)}"]
    problems = []
    if df.isna().any().any():
        problems.append(f"空の値があります（{int(df.isna().sum().sum())}件）")
    if not df["order_id"].is_unique:
        problems.append("order_id が重複しています: " + ", ".join(df.loc[df["order_id"].duplicated(), "order_id"].head(3)))
    for column, allowed in (("customer_type", CUSTOMER_TYPES), ("category", CATEGORIES), ("region", REGIONS),
                            ("status", STATUSES), ("review_flag", REVIEW_FLAGS)):
        bad = sorted(set(df[column].dropna().astype(str)) - set(allowed))
        if bad:
            problems.append(f"{column} に想定外の値があります: {bad}")
    for key, attrs in (("customer_id", ["customer_name", "customer_type"]), ("product_id", ["product_name", "category"])):
        inconsistent = df.groupby(key)[attrs].nunique().gt(1).any(axis=1)
        if inconsistent.any():  # 同じ ID で属性が食い違うと、表に分けられない
            problems.append(f"{key} ごとに {'・'.join(attrs)} が一致しない行があります: " + ", ".join(inconsistent[inconsistent].index[:3]))
    if pd.to_datetime(df["order_date"], format="%Y-%m-%d", errors="coerce").isna().any():
        problems.append("order_date が YYYY-MM-DD の形式でない行があります")
    cancelled = df["status"] == "キャンセル"
    if (df.loc[cancelled, "sales_amount"] != 0).any():
        problems.append("キャンセル注文の sales_amount が 0 でない行があります（BR-02）")
    if (df.loc[~cancelled, "sales_amount"] == 0).any():
        problems.append("キャンセルでない注文の sales_amount が 0 の行があります")
    return problems


def import_dataframe(df: pd.DataFrame, db_path: Path | str | None = None, source_sha256: str = "") -> int:
    """検査したうえで、1回のトランザクションでテーブルを作り直す。途中で失敗したら、元のデータは変わらない。"""
    db_path = db_path or DB_PATH  # 既定値は呼び出し時に決める（定義時に固定すると、DB_PATH の差し替えが効かない）
    problems = validate_frame(df)
    if problems:
        raise DataImportError("取り込めません: " + " / ".join(problems))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    customers = [tuple(_py(v) for v in r) for r in df[["customer_id", "customer_name", "customer_type"]].drop_duplicates("customer_id").itertuples(index=False, name=None)]
    products = [tuple(_py(v) for v in r) for r in df[["product_id", "product_name", "category"]].drop_duplicates("product_id").itertuples(index=False, name=None)]
    order_columns = [c for c in COLUMNS if c not in ("customer_name", "customer_type", "product_name", "category")]
    orders = [tuple(_py(v) for v in r) for r in df[order_columns].itertuples(index=False, name=None)]
    with closing(sqlite3.connect(str(db_path), timeout=30)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")  # 存在しない顧客・商品を指す注文を、DB が拒否する
        with conn:
            # sales_orders は、旧スキーマ（第9章）では表、現在は VIEW。どちらでも作り直せるよう、種類を調べてから消す
            kind = conn.execute("SELECT type FROM sqlite_master WHERE name = 'sales_orders'").fetchone()
            if kind:
                conn.execute(f"DROP {kind[0].upper()} sales_orders")
            for statement in ("DROP TABLE IF EXISTS orders", "DROP TABLE IF EXISTS customers",
                              "DROP TABLE IF EXISTS products", "DROP TABLE IF EXISTS import_meta"):
                conn.execute(statement)
            conn.executescript(_SCHEMA)
            conn.executemany("INSERT INTO customers VALUES (?, ?, ?)", customers)
            conn.executemany("INSERT INTO products VALUES (?, ?, ?)", products)
            conn.executemany(f"INSERT INTO orders ({', '.join(order_columns)}) VALUES ({', '.join('?' * len(order_columns))})", orders)
            conn.execute("INSERT INTO import_meta VALUES (?, ?, ?)",
                         (source_sha256, len(orders), datetime.now(timezone.utc).isoformat(timespec="seconds")))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    rows = orders
    return len(rows)


def _py(value):
    """numpy の型を、SQLite が受け取れる Python の型へ変える。"""
    return value.item() if hasattr(value, "item") else value


def import_csv(csv_path: Path = CSV_PATH, db_path: Path | str | None = None) -> int:
    digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest() if Path(csv_path).exists() else ""
    return import_dataframe(load_sales(csv_path), db_path, digest)


def shape_sales_by(dimension: str, rows: list[tuple]) -> list[dict]:
    """集計の行（キー・売上・件数）を、構成比と累積つきの辞書にする。どの DB の実装でも同じ形・同じ丸めにするため、共有する。"""
    total = sum(int(sales) for _, sales, _ in rows)
    result, running = [], 0
    for key, sales, _ in rows:
        running += int(sales)  # 累積は、丸めた構成比を足さず、金額を足してから割る（丸めの誤差を持ち込まない）
        result.append({dimension: key, "sales": int(sales),
                       "share_pct": round(int(sales) / total * 100, 1) if total else 0.0,
                       "cumulative_share_pct": round(running / total * 100, 1) if total else 0.0,
                       "cumulative_sales": running})
    return result


def shape_data_quality(count, start, end, cancelled, review, over_total, cancelled_with_sales, orphans, imported_at) -> dict:
    pct = lambda n: round(int(n) / count * 100, 1) if count else 0.0  # noqa: E731
    return {
        "order_count": count, "period_start": start, "period_end": end, "imported_at": imported_at,
        "cancelled_count": int(cancelled), "cancelled_share_pct": pct(cancelled),
        "review_count": int(review), "review_share_pct": pct(review),
        "sales_exceeds_total_count": int(over_total), "cancelled_with_sales_count": int(cancelled_with_sales),
        "orders_without_customer_or_product": int(orphans),
    }


class SalesRepository:
    """売上の参照。読み取り専用の接続で、目的ごとに固定した SQL だけを実行する。"""

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self._path = Path(db_path)
        if not self._path.exists():
            raise FileNotFoundError(f"売上データベースが見つかりません: {self._path}（import_csv で作成します）")
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:  # 古いスキーマで動くと、「表がない」という分かりにくいエラーになる
            raise SchemaVersionError(
                f"データベースのスキーマが古い（または新しい）です: {self._path}（version {version}、必要なのは {SCHEMA_VERSION}）。"
                "CSV から作り直すには `uv run python chapters/ch09-sqlite/import_csv.py` を実行してください"
            )

    @classmethod
    def default(cls):
        """既定の DB。DATABASE_URL があれば PostgreSQL（第15章。取り込みは明示的に行う。本番の DB へ、自動で CSV を入れない）。
        なければ SQLite で、DB がなければ CSV から取り込む。以後は DB が正本で、CSV を更新しても自動では反映しない。
        """
        from shared.postgres import database_url
        if database_url():
            from shared.postgres_sales import PostgresSalesRepository
            return PostgresSalesRepository()
        if not DB_PATH.exists():
            logger.info("sales.db がないため、CSV から取り込みます")
            import_csv()
        return cls(DB_PATH)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)  # 書き込みはできない（最小権限）
        try:
            yield conn
        finally:
            conn.close()

    def summary(self) -> dict:
        with self._connect() as conn:
            count, total, cancelled, review = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(sales_amount), 0), "
                "COALESCE(SUM(status = 'キャンセル'), 0), COALESCE(SUM(review_flag = '要レビュー'), 0) FROM orders"
            ).fetchone()
        return {"order_count": count, "total_sales": int(total), "cancelled_count": int(cancelled), "review_count": int(review)}

    def data_quality(self) -> dict:
        """データ品質の事実（第11章）。データベースの制約が守れない種類のものを、決定論的に集める。

        欠損・重複・外部キー・キャンセルの売上は、スキーマの制約（第9・10章）が守っている。ここでは、それが実際に守られていること
        （0件のはず）に加えて、制約では守れないもの（期間・取り込みの時刻・キャンセルや要レビューの割合）を返す。
        """
        with self._connect() as conn:
            count, start, end, cancelled, review, over_total, cancelled_with_sales = conn.execute(
                "SELECT COUNT(*), MIN(order_date), MAX(order_date), COALESCE(SUM(status = 'キャンセル'), 0), "
                "COALESCE(SUM(review_flag = '要レビュー'), 0), COALESCE(SUM(sales_amount > total_amount), 0), "
                "COALESCE(SUM(status = 'キャンセル' AND sales_amount <> 0), 0) FROM orders"
            ).fetchone()
            orphans = conn.execute(
                "SELECT COUNT(*) FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id "
                "LEFT JOIN products p ON p.product_id = o.product_id WHERE c.customer_id IS NULL OR p.product_id IS NULL"
            ).fetchone()[0]
            imported_at = (conn.execute("SELECT imported_at FROM import_meta").fetchone() or ("",))[0]
        return shape_data_quality(count, start, end, cancelled, review, over_total, cancelled_with_sales, orphans, imported_at)

    def sales_by(self, dimension: str) -> list[dict]:
        """許可された軸（shared/join_policy.py の DIMENSIONS）で売上を集計し、売上の大きい順に返す。構成比(%)と、大きい順の累積売上と累積構成比(%)も返す（「上位2つで○%」を LLM が足して作らずにすむよう、Tool が返す。BR-03）。

        軸によっては、別の表との結合が必要になる。結合は許可されたものだけで、結合の前後で合計と件数が変わらないことを確かめる。
        軸の名前は SQL に入らない（許可リストから SQL を組み立てる）。
        """
        plan = plan_for(dimension)  # 許可されていない軸・結合は、ここで JoinNotAllowed（ValueError）
        with self._connect() as conn:
            if plan.edge:
                validate_edge(conn, plan.edge)  # 静的な検査: 結合先のキーが一意か
            rows = conn.execute(aggregate_sql(plan)).fetchall()
            fact_sales, fact_orders = conn.execute("SELECT COALESCE(SUM(sales_amount), 0), COUNT(*) FROM orders").fetchone()
        validate_totals(rows, int(fact_sales), int(fact_orders))  # 実行時の検査: 行が増えたり減ったりしていないか
        return shape_sales_by(dimension, rows)

    def preview(self, dimension: str, limit: int = 5) -> list[dict]:
        """結合の形を確かめるための、少数の行（Preview）。集計の前に、結合が意図どおりかを目で見られる。"""
        if not 1 <= limit <= 20:
            raise ValueError("limit は1〜20にしてください")
        plan = plan_for(dimension)
        with self._connect() as conn:
            if plan.edge:
                validate_edge(conn, plan.edge)
            rows = conn.execute(preview_sql(plan), (limit,)).fetchall()
        return [{"order_id": o, dimension: k, "sales_amount": int(a)} for o, k, a in rows]

    def get_order(self, order_id: str) -> dict | None:
        """注文を1件返す。order_id はパラメータで渡す（SQL の一部にならない）。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(f"{_SELECT_ALL} WHERE o.order_id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def to_dataframe(self) -> pd.DataFrame:
        """データの概要づくりなど、全件を見る処理のため。集計には使わない（集計は SQL）。"""
        with self._connect() as conn:
            return pd.read_sql_query(f"{_SELECT_ALL} ORDER BY o.rowid", conn)
