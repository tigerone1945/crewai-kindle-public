"""全テスト共通: 売上DBを一時ファイルに取り込み、開発用の data/sales.db に触れない（テストの独立性）。"""
import os

import pytest

import shared.sales_db as sales_db

_DATABASE_ENV = ("DATABASE_URL", "DATABASE_URL_FILE", "DATABASE_SCHEMA")


@pytest.fixture(scope="session", autouse=True)
def _no_database_env_at_session_start():
    """開発者のシェルに DATABASE_URL があっても、モジュール・セッション単位の fixture（関数単位より先に動く）が、PostgreSQL を見ない。"""
    saved = {k: os.environ.pop(k) for k in _DATABASE_ENV if k in os.environ}
    yield
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _restore_environment():
    """テストが os.environ を直接書き換えても（起動の処理は、ファイルの秘密情報を環境へ出す）、次のテストへ残さない。"""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


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
