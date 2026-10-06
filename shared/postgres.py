"""PostgreSQL への接続（第15章）: 接続プールと、接続・トランザクションの扱い。

方針:
- 接続先は、環境変数 DATABASE_URL（パスワードを含む）。コードにも、イメージにも、ログにも、エラー文にも出さない。
- 接続は、プール（psycopg_pool）から借りる。リクエストごとに接続を作ると、接続の確立が毎回かかり、同時実行で DB の接続数の上限を超える。
- 1回の借用が、1つのトランザクション。正常に終われば COMMIT、例外が出れば ROLLBACK（途中まで書いたものは残らない）。
- 参照だけの接続は、読み取り専用のトランザクション（default_transaction_read_only）。書き込みは DB が拒否する（最小権限）。
- スキーマ名で、同じ DB の中を分けられる（テストや、環境ごとの分離）。名前は、固定の形式だけ受け付ける（SQL に入るため）。
- 接続できないときは、URL もパスワードも含まない、固定の文で知らせる（DatabaseUnavailable）。
"""
import atexit
import logging
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger(__name__)

_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
DEFAULT_POOL_MAX = 10
CONNECT_TIMEOUT_SECONDS = 5
POOL_WAIT_SECONDS = 10  # プールに空きがないとき、借りるまで待つ時間


class DatabaseUnavailable(RuntimeError):
    """DB に接続できない、またはプールから借りられない。接続先の情報（URL・パスワード）は含まない。"""


def database_url() -> str | None:
    """利用する PostgreSQL の URL。未設定なら None（第14章までの SQLite を使う）。DATABASE_URL、またはファイルを指す DATABASE_URL_FILE（第16章）。"""
    from shared.config import read_secret
    return read_secret("DATABASE_URL")


def database_schema() -> str | None:
    return os.getenv("DATABASE_SCHEMA") or None


def check_schema_name(name: str) -> str:
    if not _SCHEMA_NAME.match(name):
        raise ValueError("スキーマ名は、小文字で始まる、小文字・数字・アンダースコアの41文字以内にしてください")
    return name


class Database:
    """接続プールを持つ。スレッドから安全に使える（API は、スレッドで分析を実行する。第14章）。"""

    def __init__(self, url: str, *, schema: str | None = None, read_only: bool = False, max_size: int | None = None) -> None:
        options = []
        if schema:
            options.append(f"-c search_path={check_schema_name(schema)}")
        if read_only:
            options.append("-c default_transaction_read_only=on")
        self._schema = schema
        self._pool = ConnectionPool(
            url, min_size=1, max_size=max_size or int(os.getenv("DB_POOL_MAX", DEFAULT_POOL_MAX)), open=False, timeout=POOL_WAIT_SECONDS,
            kwargs={"connect_timeout": CONNECT_TIMEOUT_SECONDS, "options": " ".join(options) or None},
        )
        self._opened = False
        self._lock = threading.Lock()

    def _ensure_open(self) -> None:
        if self._opened:
            return
        with self._lock:
            if self._opened:
                return
            try:
                self._pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS + 5)
            except (PoolTimeout, psycopg.Error):
                self._pool.close()
                logger.error("database connection failed")  # 接続先の情報は、出さない
                raise DatabaseUnavailable("データベースに接続できません。接続先の設定と、データベースの状態を確認してください。") from None
            self._opened = True

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """プールから借りる。正常に終われば COMMIT、例外なら ROLLBACK して、プールへ返す。"""
        self._ensure_open()
        try:
            with self._pool.connection() as conn:
                yield conn
        except PoolTimeout:
            raise DatabaseUnavailable("データベースが混み合っています。しばらくしてから、もう一度お試しください。") from None
        except psycopg.OperationalError as e:
            if isinstance(e, (psycopg.errors.QueryCanceled, psycopg.errors.DeadlockDetected)):
                raise
            logger.error("database operation failed type=%s", type(e).__name__)
            raise DatabaseUnavailable("データベースとの通信に失敗しました。しばらくしてから、もう一度お試しください。") from None

    def close(self) -> None:
        if self._opened:
            self._pool.close()
            self._opened = False

    def stats(self) -> dict:
        return self._pool.get_stats() if self._opened else {}


_registry: dict[tuple, Database] = {}
_registry_lock = threading.Lock()


def get_database(url: str | None = None, *, schema: str | None = None, read_only: bool = False) -> Database:
    """同じ接続先・スキーマ・種類には、同じプールを使い回す。"""
    url = url or database_url()
    if not url:
        raise DatabaseUnavailable("DATABASE_URL が設定されていません。")
    key = (url, schema or database_schema(), read_only)
    with _registry_lock:
        if key not in _registry:
            _registry[key] = Database(url, schema=key[1], read_only=read_only)
        return _registry[key]


def close_all() -> None:
    """すべてのプールを閉じる。プロセスの終了時にも呼ぶ（閉じないと、ワーカースレッドの停止待ちで警告が出る）。"""
    with _registry_lock:
        for db in _registry.values():
            db.close()
        _registry.clear()


atexit.register(close_all)
