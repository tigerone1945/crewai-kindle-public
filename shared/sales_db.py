"""売上データベース（第9章）: CSV を SQLite へ取り込み、目的限定のクエリだけを公開する。

- 取り込み（import_csv）: CSV を検証して、1回のトランザクションで置き換える。CSV は初期データであり、以後の正本は DB（BR-07）。
- 参照（SalesRepository）: Agent の Tool が使う入口。読み取り専用の接続で、SQL は目的ごとに固定し、値は必ずパラメータで渡す（BR-04）。
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

from shared.sales_data import CSV_PATH, ROOT, load_sales

logger = logging.getLogger(__name__)

DB_PATH = ROOT / "data" / "sales.db"

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


_SCHEMA = f"""
CREATE TABLE sales_orders (
    order_id      TEXT PRIMARY KEY,
    order_date    TEXT NOT NULL,
    customer_id   TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ({_in(CUSTOMER_TYPES)})),
    product_id    TEXT NOT NULL,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL CHECK (category IN ({_in(CATEGORIES)})),
    unit_price    INTEGER NOT NULL CHECK (unit_price >= 0),
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
CREATE INDEX idx_sales_orders_region ON sales_orders (region);
CREATE INDEX idx_sales_orders_category ON sales_orders (category);
CREATE TABLE import_meta (source_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL, imported_at TEXT NOT NULL);
"""


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
    rows = [tuple(_py(v) for v in row) for row in df[list(COLUMNS)].itertuples(index=False, name=None)]
    with closing(sqlite3.connect(str(db_path), timeout=30)) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS sales_orders")
        conn.execute("DROP TABLE IF EXISTS import_meta")
        conn.executescript(_SCHEMA)
        conn.executemany(f"INSERT INTO sales_orders ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", rows)
        conn.execute("INSERT INTO import_meta VALUES (?, ?, ?)",
                     (source_sha256, len(rows), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return len(rows)


def _py(value):
    """numpy の型を、SQLite が受け取れる Python の型へ変える。"""
    return value.item() if hasattr(value, "item") else value


def import_csv(csv_path: Path = CSV_PATH, db_path: Path | str | None = None) -> int:
    digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest() if Path(csv_path).exists() else ""
    return import_dataframe(load_sales(csv_path), db_path, digest)


class SalesRepository:
    """売上の参照。読み取り専用の接続で、目的ごとに固定した SQL だけを実行する。"""

    # 識別子（列名）はパラメータにできないため、許可した列ごとに SQL を固定する。利用者や Agent の入力は SQL に入らない
    _SALES_BY = {
        column: f"SELECT {column}, SUM(sales_amount) AS sales FROM sales_orders GROUP BY {column} ORDER BY sales DESC, {column}"
        for column in ("category", "region")
    }

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self._path = Path(db_path)
        if not self._path.exists():
            raise FileNotFoundError(f"売上データベースが見つかりません: {self._path}（import_csv で作成します）")

    @classmethod
    def default(cls) -> "SalesRepository":
        """既定の DB。なければ CSV から取り込む。以後は DB が正本で、CSV を更新しても自動では反映しない。"""
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
                "COALESCE(SUM(status = 'キャンセル'), 0), COALESCE(SUM(review_flag = '要レビュー'), 0) FROM sales_orders"
            ).fetchone()
        return {"order_count": count, "total_sales": int(total), "cancelled_count": int(cancelled), "review_count": int(review)}

    def sales_by(self, column: str) -> list[dict]:
        """指定列（許可された列のみ）で売上を集計し、売上の大きい順に返す。構成比(%)も返す。"""
        if column not in self._SALES_BY:
            raise ValueError(f"集計できない列です: {column}（許可: {tuple(self._SALES_BY)}）")
        with self._connect() as conn:
            rows = conn.execute(self._SALES_BY[column]).fetchall()
        total = sum(int(sales) for _, sales in rows)
        return [{column: key, "sales": int(sales), "share_pct": round(int(sales) / total * 100, 1) if total else 0.0}
                for key, sales in rows]

    def get_order(self, order_id: str) -> dict | None:
        """注文を1件返す。order_id はパラメータで渡す（SQL の一部にならない）。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sales_orders WHERE order_id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def to_dataframe(self) -> pd.DataFrame:
        """データの概要づくりなど、全件を見る処理のため。集計には使わない（集計は SQL）。"""
        with self._connect() as conn:
            return pd.read_sql_query("SELECT * FROM sales_orders ORDER BY rowid", conn)
