"""Flow の状態の永続化（PostgreSQL 版。第15章）。承認待ちの Flow を、プロセスやコンテナを越えて再開できるようにする。

CrewAI の `SQLiteFlowPersistence` と同じ仕組みを、PostgreSQL で実装する（`FlowPersistence` の実装）。
アプリのコンテナは、状態を持たない（Stateless）。再起動しても、別のコンテナが引き継いでも、承認待ちは DB に残る。
"""
import json
from datetime import datetime, timezone
from typing import Any

from crewai.flow.async_feedback.types import PendingFeedbackContext
from crewai.flow.persistence.base import FlowPersistence
from pydantic import BaseModel, Field, PrivateAttr, model_validator
from typing_extensions import Self

from shared.postgres import Database, get_database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_states (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    flow_uuid TEXT NOT NULL, method_name TEXT NOT NULL, timestamp TIMESTAMPTZ NOT NULL, state_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flow_states_uuid ON flow_states (flow_uuid);
CREATE TABLE IF NOT EXISTS pending_feedback (
    flow_uuid TEXT PRIMARY KEY, context_json JSONB NOT NULL, state_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL
);
"""
_SCHEMA_LOCK_KEY = 15_003


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


class PostgresFlowPersistence(FlowPersistence):
    persistence_type: str = Field(default="PostgresFlowPersistence")
    _db: Database = PrivateAttr()

    def __init__(self, db: Database | None = None, /, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._db = db or get_database()
        self.init_db()

    @model_validator(mode="after")
    def _noop(self) -> Self:
        return self

    def init_db(self) -> None:
        with self._db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
            conn.execute(_SCHEMA)

    @staticmethod
    def _to_state_dict(state_data: dict[str, Any] | BaseModel) -> dict[str, Any]:
        if isinstance(state_data, BaseModel):
            return state_data.model_dump(mode="json")
        if isinstance(state_data, dict):
            return state_data
        raise ValueError(f"state_data must be either a Pydantic BaseModel or dict, got {type(state_data)}")

    def save_state(self, flow_uuid: str, method_name: str, state_data: dict[str, Any] | BaseModel) -> None:
        with self._db.connection() as conn:
            conn.execute("INSERT INTO flow_states (flow_uuid, method_name, timestamp, state_json) VALUES (%s, %s, %s, %s)",
                         (flow_uuid, method_name, datetime.now(timezone.utc), _dumps(self._to_state_dict(state_data))))

    def load_state(self, flow_uuid: str) -> dict[str, Any] | None:
        with self._db.connection() as conn:
            row = conn.execute("SELECT state_json FROM flow_states WHERE flow_uuid = %s ORDER BY id DESC LIMIT 1", (flow_uuid,)).fetchone()
        return row[0] if row and isinstance(row[0], dict) else None

    def save_pending_feedback(self, flow_uuid: str, context: PendingFeedbackContext, state_data: dict[str, Any] | BaseModel) -> None:
        state = _dumps(self._to_state_dict(state_data))
        now = datetime.now(timezone.utc)
        with self._db.connection() as conn:  # 状態の保存と、承認待ちの印は、1つのトランザクション
            conn.execute("INSERT INTO flow_states (flow_uuid, method_name, timestamp, state_json) VALUES (%s, %s, %s, %s)",
                         (flow_uuid, context.method_name, now, state))
            conn.execute(
                "INSERT INTO pending_feedback (flow_uuid, context_json, state_json, created_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (flow_uuid) DO UPDATE SET context_json = EXCLUDED.context_json, state_json = EXCLUDED.state_json, created_at = EXCLUDED.created_at",
                (flow_uuid, _dumps(context.to_dict()), state, now))

    def load_pending_feedback(self, flow_uuid: str) -> tuple[dict[str, Any], PendingFeedbackContext] | None:
        with self._db.connection() as conn:
            row = conn.execute("SELECT state_json, context_json FROM pending_feedback WHERE flow_uuid = %s", (flow_uuid,)).fetchone()
        if row:
            return row[0], PendingFeedbackContext.from_dict(row[1])
        return None

    def clear_pending_feedback(self, flow_uuid: str) -> None:
        with self._db.connection() as conn:
            conn.execute("DELETE FROM pending_feedback WHERE flow_uuid = %s", (flow_uuid,))
