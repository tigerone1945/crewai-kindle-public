"""第9章 SQL インジェクションのデモ: 文字列連結のクエリと、パラメータ化したクエリを、同じ入力で比べる。

教材用。一時ファイルの DB だけを使い、data/sales.db には触れない。
実行: uv run python chapters/ch09-sqlite/injection_demo.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sales_db import SalesRepository, import_csv  # noqa: E402

EVIL = "O0001' OR '1'='1"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sales.db"
        import_csv(db_path=db)

        # 悪い例: 入力を SQL の文字列に連結する。条件が常に真になり、全件が返る
        with sqlite3.connect(db) as conn:
            naive = conn.execute(f"SELECT order_id FROM sales_orders WHERE order_id = '{EVIL}'").fetchall()
        print(f"連結したクエリ    : {len(naive)}件が返った（入力が SQL の一部になった）")

        # 良い例: 入力はパラメータ。SQL の一部にならず、ただの文字列として照合される
        safe = SalesRepository(db).get_order(EVIL)
        print(f"パラメータ化した  : {safe}（該当なし）")


if __name__ == "__main__":
    main()
