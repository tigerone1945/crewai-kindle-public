"""第9章 CSV から SQLite へ取り込む。

CSV を検証して data/sales.db を作る（作り直す）。検査に通らなければ、何も変更せず、理由を表示して終了する。
実行: uv run python chapters/ch09-sqlite/import_csv.py [CSVのパス]
"""
import sys
from pathlib import Path

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sales_data import CSV_PATH  # noqa: E402
from shared.sales_db import DB_PATH, DataImportError, SalesRepository, import_csv  # noqa: E402


def main() -> int:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CSV_PATH
    try:
        count = import_csv(csv_path)
    except (DataImportError, FileNotFoundError) as e:
        print(f"取り込みに失敗しました（DBは変更していません）: {e}", file=sys.stderr)
        return 1
    summary = SalesRepository(DB_PATH).summary()
    print(f"{count}件を取り込みました: {DB_PATH}")
    print(f"確認: 売上合計 {summary['total_sales']:,}円 / キャンセル {summary['cancelled_count']}件 / 要レビュー {summary['review_count']}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
