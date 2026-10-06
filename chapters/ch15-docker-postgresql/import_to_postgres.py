"""CSV を PostgreSQL へ取り込む（第15章）。初回の移行用。

既定では、データベースが初期化済みなら、何もしない。DB が正本（BR-07）なので、起動のたびに作り直して、運用中のデータを消さないため。
作り直すには `--force`（テーブルを作り直す。承認の記録・Flow の状態は消えない）。
使い方: DATABASE_URL=postgresql://... uv run python chapters/ch15-docker-postgresql/import_to_postgres.py [--force]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.postgres import DatabaseUnavailable, get_database, database_url  # noqa: E402
from shared.postgres_sales import import_csv_postgres  # noqa: E402
from shared.sales_db import SCHEMA_VERSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import_to_postgres")


def initialized(db) -> bool:
    with db.connection() as conn:
        if not conn.execute("SELECT to_regclass('schema_version') IS NOT NULL").fetchone()[0]:
            return False
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    return bool(row) and row[0] == SCHEMA_VERSION


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="初期化済みでも、CSV から作り直す")
    args = parser.parse_args()
    if not database_url():
        log.error("DATABASE_URL が設定されていません")
        return 2
    try:
        db = get_database()
        if initialized(db) and not args.force:
            log.info("すでに初期化されています。何もしません（作り直すには --force）")
            return 0
        log.info("取り込みました: %d 件", import_csv_postgres(db))
    except DatabaseUnavailable as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
