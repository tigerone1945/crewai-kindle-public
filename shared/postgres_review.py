"""レビュー依頼の登録と監査ログの PostgreSQL 版（第15章）。SQLite 版（shared/review_requests.py）と、同じ入口。

- 二重登録は、主キー（order_id）で防ぐ。同時に2つの承認が来ても、登録されるのは1件（DB が決める。アプリのロックに頼らない）。
- 登録と監査ログ（executed）は、1つのトランザクション。片方だけが残ることはない。
- 時刻は TIMESTAMPTZ（UTC）。取り出すときは、SQLite 版と同じ ISO 8601 の文字列にする。
"""
from datetime import datetime, timezone

import psycopg

from shared.memory_policy import check_actor_id
from shared.postgres import Database, get_database
from shared.review_requests import RESOLVED_EVENTS, DuplicateRequest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_requests (
    order_id     TEXT PRIMARY KEY,  -- 同じ注文への二重登録を、DB の制約で防ぐ
    requested_by TEXT NOT NULL, approved_by TEXT NOT NULL, flow_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS review_decisions (
    flow_id    TEXT PRIMARY KEY,  -- 1つの承認待ちに対する決定は、1つだけ。同時の決定を、DB の制約で1つに絞る
    decided_by TEXT NOT NULL, decision TEXT NOT NULL, decided_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at       TIMESTAMPTZ NOT NULL, flow_id TEXT NOT NULL, event TEXT NOT NULL,
    order_id TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_log_flow ON audit_log (flow_id);
"""
_SCHEMA_LOCK_KEY = 15_002


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class PostgresReviewStore:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_database()
        with self._db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))  # 複数のプロセスが同時に起動しても、表の作成が競合しない
            conn.execute(_SCHEMA)

    def create_request(self, order_id: str, requested_by: str, approved_by: str, flow_id: str) -> None:
        """High Risk な書き込み。承認を得た Flow からだけ呼ぶ。登録と監査ログ（executed）は、1つのトランザクション。"""
        check_actor_id(requested_by), check_actor_id(approved_by)
        now = datetime.now(timezone.utc)
        try:
            with self._db.connection() as conn:
                conn.execute("INSERT INTO review_requests VALUES (%s, %s, %s, %s, %s)", (order_id, requested_by, approved_by, flow_id, now))
                conn.execute("INSERT INTO audit_log (at, flow_id, event, order_id, actor, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                             (now, flow_id, "executed", order_id, approved_by, ""))
        except psycopg.errors.UniqueViolation as e:
            raise DuplicateRequest(f"注文 {order_id} のレビュー依頼はすでにあります") from e

    def claim_decision(self, flow_id: str, decided_by: str, decision: str) -> bool:
        """承認待ちへの決定の権利を、1人だけが得る。得られなければ False（すでに他の人が決めている）。"""
        with self._db.connection() as conn:
            row = conn.execute("INSERT INTO review_decisions VALUES (%s, %s, %s, %s) ON CONFLICT (flow_id) DO NOTHING RETURNING flow_id",
                               (flow_id, decided_by, decision, datetime.now(timezone.utc))).fetchone()
        return row is not None

    def release_decision(self, flow_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute("DELETE FROM review_decisions WHERE flow_id = %s", (flow_id,))

    def list_requests(self) -> list[dict]:
        with self._db.connection() as conn:
            rows = conn.execute("SELECT order_id, requested_by, approved_by, flow_id, created_at FROM review_requests ORDER BY created_at, order_id").fetchall()
        return [dict(zip(("order_id", "requested_by", "approved_by", "flow_id", "created_at"), (*r[:4], _iso(r[4])))) for r in rows]

    def audit(self, event: str, flow_id: str, order_id: str, actor: str, detail: str = "") -> None:
        with self._db.connection() as conn:
            conn.execute("INSERT INTO audit_log (at, flow_id, event, order_id, actor, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                         (datetime.now(timezone.utc), flow_id, event, order_id, actor, detail))

    def audit_log(self, flow_id: str | None = None) -> list[dict]:
        query, args = "SELECT at, flow_id, event, order_id, actor, detail FROM audit_log", ()
        if flow_id:
            query, args = query + " WHERE flow_id = %s", (flow_id,)
        with self._db.connection() as conn:
            rows = conn.execute(query + " ORDER BY id", args).fetchall()
        return [dict(zip(("at", "flow_id", "event", "order_id", "actor", "detail"), (_iso(r[0]), *r[1:]))) for r in rows]

    def pending_approvals(self) -> list[dict]:
        """提案されて、まだ承認も却下もされていない依頼。"""
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT p.flow_id, p.order_id, p.actor FROM audit_log p WHERE p.event = 'proposed' AND NOT EXISTS "
                "(SELECT 1 FROM audit_log r WHERE r.flow_id = p.flow_id AND r.event = ANY(%s)) ORDER BY p.id", (list(RESOLVED_EVENTS),)).fetchall()
        return [{"flow_id": f, "order_id": o, "requested_by": a} for f, o, a in rows]
