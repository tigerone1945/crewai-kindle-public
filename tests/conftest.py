"""全テスト共通: 売上DBを一時ファイルに取り込み、開発用の data/sales.db に触れない（テストの独立性）。"""
import pytest

import shared.sales_db as sales_db


@pytest.fixture(scope="session", autouse=True)
def isolated_sales_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("sales") / "sales.db"
    sales_db.import_csv(db_path=path)
    patch = pytest.MonkeyPatch()
    patch.setattr(sales_db, "DB_PATH", path)
    yield path
    patch.undo()


@pytest.fixture(autouse=True)
def _sqlite_unless_postgres_test(monkeypatch):
    """開発者のシェルに DATABASE_URL があっても、通常のテストは SQLite（一時ファイル）で動かす。PostgreSQL を使うテストは、自分で設定する。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SCHEMA", raising=False)
