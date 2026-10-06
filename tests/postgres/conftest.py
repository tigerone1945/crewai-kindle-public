"""PostgreSQL の実機を使うテスト（第15章）。TEST_DATABASE_URL が未設定、または接続できなければ、スキップする。

起動例: docker run -d --name crewai-kindle-pg -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=sales -p 127.0.0.1:55432:5432 postgres:17
        export TEST_DATABASE_URL=postgresql://postgres:devpassword@127.0.0.1:55432/sales
テストごとに、専用のスキーマを作って、終わったら消す（テスト同士が影響しない。同じ DB を、開発用のデータと共用しても壊さない）。
"""
import os
import uuid

import psycopg
import pytest

from shared.postgres import Database, close_all
from shared.postgres_sales import PostgresSalesRepository, import_csv_postgres

URL = os.getenv("TEST_DATABASE_URL")


def _reachable() -> bool:
    if not URL:
        return False
    try:
        with psycopg.connect(URL, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


def pytest_collection_modifyitems(config, items):
    if not _reachable():
        skip = pytest.mark.skip(reason="PostgreSQL（TEST_DATABASE_URL）に接続できないためスキップ")
        for item in items:
            if "tests/postgres" in str(item.fspath):
                item.add_marker(skip)


@pytest.fixture
def pg_schema():
    name = f"t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {name}")
    yield name
    close_all()
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def pg_db(pg_schema):
    db = Database(URL, schema=pg_schema, max_size=8)
    yield db
    db.close()


@pytest.fixture
def pg_sales(pg_db, pg_schema, monkeypatch):
    """取り込み済みの売上。SalesRepository.default() も、このスキーマの PostgreSQL を返す（承認 Flow の対象の検査も、PostgreSQL を読む）。"""
    import_csv_postgres(pg_db)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_SCHEMA", pg_schema)
    reader = Database(URL, schema=pg_schema, read_only=True)
    yield PostgresSalesRepository(reader)
    reader.close()
