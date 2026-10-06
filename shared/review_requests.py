"""レビュー依頼の登録（第8章）: 状態を変える処理（Side Effect）と、その監査ログ。

分類（BR-05）:
- Read-only: 売上の集計 Tool（shared/sales_tools.py）。Agent が自由に呼んでよい。
- High Risk: レビュー依頼の登録（このモジュールの create_request）。Agent には Tool として渡さない。
  Flow が、人の承認を得たあとにだけ呼ぶ（shared/review_flow.py）。
Business Data の正本は CSV のまま。ここで書くのは「レビュー依頼」というアプリケーションの状態だけ（第9章でDatabaseへ移す）。

監査ログ（Audit）は Trace と別物。Trace は「どう動いたか」の観測で、捨てても業務は成り立つ。
監査ログは「誰が・何を・いつ決めたか」の記録で、依頼や承認の根拠として残す。質問文や自由記述は書かない。
"""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from shared.memory_policy import check_actor_id
from shared.sales_data import ROOT, load_sales

DEFAULT_DB = ROOT / "data" / "review.db"
ORDER_ID = re.compile(r"^O\d{4}$")

# 決定（承認待ちを抜ける）を表す監査イベント
RESOLVED_EVENTS = ("approved", "rejected", "invalid")


class DuplicateRequest(RuntimeError):
    """同じ注文のレビュー依頼がすでにある。"""


class InvalidReviewTarget(ValueError):
    """レビュー依頼の対象にできない注文。`code` は理由の種類、`message` は利用者へ見せてよい文。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_review_target(order_id: str) -> dict:
    """レビュー依頼の対象にできる注文なら、承認者に見せる事実（個人名を含まない）を返す。できなければ InvalidReviewTarget。"""
    if not ORDER_ID.match(order_id):
        raise InvalidReviewTarget("bad_format", "注文IDの形式が正しくありません（例: O0001）。")
    df = load_sales()
    rows = df[df["order_id"] == order_id]
    if rows.empty:
        raise InvalidReviewTarget("not_found", f"注文 {order_id} は見つかりません。")
    row = rows.iloc[0]
    if row["review_flag"] == "要レビュー":
        raise InvalidReviewTarget("already_flagged", f"注文 {order_id} は、すでにレビュー対象です。")
    return {
        "order_id": order_id,
        "order_date": str(row["order_date"])[:10],
        "customer_type": str(row["customer_type"]),
        "category": str(row["category"]),
        "status": str(row["status"]),
        "sales_amount": int(row["sales_amount"]),
    }


class ReviewStore:
    def __init__(self, path: Path | str = DEFAULT_DB) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS review_requests (
                    order_id TEXT PRIMARY KEY,  -- 同じ注文への二重登録を、DB の制約で防ぐ
                    requested_by TEXT NOT NULL, approved_by TEXT NOT NULL, flow_id TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, flow_id TEXT NOT NULL, event TEXT NOT NULL,
                    order_id TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"""
            )

    @contextmanager
    def _connect(self):
        """成功ならコミット、失敗ならロールバックし、必ず接続を閉じる。"""
        conn = sqlite3.connect(self._path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_request(self, order_id: str, requested_by: str, approved_by: str, flow_id: str) -> None:
        """High Risk な書き込み。承認を得た Flow からだけ呼ぶ。"""
        check_actor_id(requested_by), check_actor_id(approved_by)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO review_requests VALUES (?, ?, ?, ?, ?)",
                    (order_id, requested_by, approved_by, flow_id, _now()),
                )
        except sqlite3.IntegrityError as e:
            raise DuplicateRequest(f"注文 {order_id} のレビュー依頼はすでにあります") from e

    def list_requests(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT order_id, requested_by, approved_by, flow_id, created_at FROM review_requests ORDER BY created_at").fetchall()
        return [dict(zip(("order_id", "requested_by", "approved_by", "flow_id", "created_at"), r)) for r in rows]

    def audit(self, event: str, flow_id: str, order_id: str, actor: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO audit_log (at, flow_id, event, order_id, actor, detail) VALUES (?, ?, ?, ?, ?, ?)",
                         (_now(), flow_id, event, order_id, actor, detail))

    def audit_log(self, flow_id: str | None = None) -> list[dict]:
        query, args = "SELECT at, flow_id, event, order_id, actor, detail FROM audit_log", ()
        if flow_id:
            query, args = query + " WHERE flow_id = ?", (flow_id,)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY id", args).fetchall()
        return [dict(zip(("at", "flow_id", "event", "order_id", "actor", "detail"), r)) for r in rows]

    def pending_approvals(self) -> list[dict]:
        """提案されて、まだ承認も却下もされていない依頼。アプリを再起動しても、ここから承認待ちを辿れる。"""
        resolved = {e["flow_id"] for e in self.audit_log() if e["event"] in RESOLVED_EVENTS}
        return [{"flow_id": e["flow_id"], "order_id": e["order_id"], "requested_by": e["actor"]}
                for e in self.audit_log() if e["event"] == "proposed" and e["flow_id"] not in resolved]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
