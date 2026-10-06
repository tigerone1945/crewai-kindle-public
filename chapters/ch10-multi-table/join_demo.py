"""第10章 結合の Preview・Many-to-Many の危険・Join Validation のデモ。

一時ファイルの DB だけを使い、data/sales.db には触れない。
実行: uv run python chapters/ch10-multi-table/join_demo.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.join_policy as jp  # noqa: E402
from shared.sales_db import SalesRepository, import_csv  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sales.db"
        import_csv(db_path=db)
        repo = SalesRepository(db)
        total = repo.summary()["total_sales"]

        print("== 1. Preview: 結合の形を、集計の前に目で確かめる（顧客種別）")
        for row in repo.preview("customer_type", 3):
            print("  ", row)

        print("\n== 2. 許可された結合での集計（結合の前後で、合計と件数が一致することを確かめてから返す）")
        for row in repo.sales_by("customer_type"):
            print("  ", row)
        print(f"   合計 {sum(r['sales'] for r in repo.sales_by('customer_type')):,} 円 = 結合前の合計 {total:,} 円")

        print("\n== 3. Many-to-Many の危険: 顧客と担当者の対応表（1顧客に複数の担当者）")
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE customer_salespersons (customer_id TEXT, salesperson TEXT)")
            conn.execute("INSERT INTO customer_salespersons SELECT DISTINCT customer_id, salesperson FROM orders")
            rows = conn.execute(
                "SELECT b.salesperson, SUM(o.sales_amount), COUNT(*) FROM orders o "
                "JOIN customer_salespersons b ON b.customer_id = o.customer_id GROUP BY b.salesperson"
            ).fetchall()
            inflated = sum(r[1] for r in rows)
            print(f"   そのまま結合して集計した合計: {inflated:,} 円（本来は {total:,} 円。{inflated / total:.1f}倍に水増し）")

            print("\n== 4. Join Validation が止める")
            try:
                jp.validate_totals(rows, total, 300)
            except jp.JoinValidationError as e:
                print("   実行時の検査:", e)
            edge = jp.JoinEdge("orders", "customer_id", "customer_salespersons", "customer_id")
            try:
                jp.validate_edge(conn, edge)
            except jp.JoinValidationError as e:
                print("   静的な検査  :", e)

        print("\n== 5. 許可されていない軸・結合は、そもそも計画できない")
        for name in ("customer_name", "salesperson"):
            try:
                jp.plan_for(name)
            except jp.JoinNotAllowed as e:
                print(f"   {name}: {e}")


if __name__ == "__main__":
    main()
