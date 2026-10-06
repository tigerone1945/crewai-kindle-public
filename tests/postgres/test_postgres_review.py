"""承認の記録・Flow の状態の PostgreSQL 版（第15章）。二重登録・トランザクション・同時の承認・再起動をまたいだ再開を、実機で確かめる。"""
import collections
import threading

import pytest

import shared.api as api
import shared.review_flow as review_flow
from shared.postgres import Database
from shared.postgres_flow_persistence import PostgresFlowPersistence
from shared.postgres_review import PostgresReviewStore
from shared.review_requests import DuplicateRequest
from tests.postgres.conftest import URL


@pytest.fixture
def stores(pg_sales, pg_db):
    """売上（検証用）と、承認の記録・Flow の状態。同じスキーマ。"""
    return PostgresReviewStore(pg_db), PostgresFlowPersistence(pg_db)


# --- ReviewStore ---

def test_registration_and_its_audit_log_are_written_together(stores):
    store, _ = stores
    store.create_request("O0001", "user-001", "approver-001", "flow-1")
    [row] = store.list_requests()
    assert (row["order_id"], row["requested_by"], row["approved_by"]) == ("O0001", "user-001", "approver-001")
    assert "T" in row["created_at"] and row["created_at"].endswith("+00:00")  # SQLite 版と同じ ISO 8601（UTC）
    assert [e["event"] for e in store.audit_log("flow-1")] == ["executed"]


def test_duplicate_registration_is_rejected_by_the_primary_key(stores):
    store, _ = stores
    store.create_request("O0001", "user-001", "approver-001", "flow-1")
    with pytest.raises(DuplicateRequest):
        store.create_request("O0001", "user-002", "approver-002", "flow-2")
    assert len(store.list_requests()) == 1
    assert [e["flow_id"] for e in store.audit_log()] == ["flow-1"]  # 失敗した登録の監査ログは、残らない


def test_a_failing_audit_write_rolls_back_the_registration(stores, pg_db):
    """登録と監査ログは1つのトランザクション。監査ログを書けなければ、登録も残らない。"""
    store, _ = stores
    with pg_db.connection() as conn:
        conn.execute("CREATE FUNCTION refuse() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit unavailable'; END $$")
        conn.execute("CREATE TRIGGER no_executed BEFORE INSERT ON audit_log FOR EACH ROW WHEN (NEW.event = 'executed') EXECUTE FUNCTION refuse()")
    with pytest.raises(Exception, match="audit unavailable"):
        store.create_request("O0001", "user-001", "approver-001", "flow-1")
    assert store.list_requests() == [] and store.audit_log() == []


def test_concurrent_registrations_of_the_same_order_register_exactly_one(stores):
    store, _ = stores
    outcomes, barrier = [], threading.Barrier(12)

    def go(i):
        barrier.wait()
        try:
            store.create_request("O0001", "user-001", f"approver-{i:03d}", f"flow-{i}")
            outcomes.append("ok")
        except DuplicateRequest:
            outcomes.append("duplicate")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert collections.Counter(outcomes) == {"ok": 1, "duplicate": 11}  # 決めるのは DB（アプリのロックではない）
    assert len(store.list_requests()) == 1 and len([e for e in store.audit_log() if e["event"] == "executed"]) == 1


def test_pending_approvals_follow_the_audit_log(stores):
    store, _ = stores
    store.audit("proposed", "f1", "O0001", "user-001")
    store.audit("proposed", "f2", "O0002", "user-001")
    store.audit("approved", "f1", "O0001", "approver-001")
    assert store.pending_approvals() == [{"flow_id": "f2", "order_id": "O0002", "requested_by": "user-001"}]


# --- 承認 Flow（状態を PostgreSQL に保存）---

ORDER = "O0001"


def start(stores, order=ORDER):
    store, persistence = stores
    return review_flow.start_review(order, "user-001", store, persistence)


def test_approval_flow_end_to_end_on_postgresql(stores):
    store, persistence = stores
    ticket = start(stores)
    assert ticket.status == "pending_approval" and store.list_requests() == []
    assert [p["flow_id"] for p in store.pending_approvals()] == [ticket.flow_id]
    assert review_flow.resolve_review(ticket.flow_id, "approve", "approver-001", store, persistence) == "registered"
    assert [r["order_id"] for r in store.list_requests()] == [ORDER]
    assert [e["event"] for e in store.audit_log(ticket.flow_id)] == ["proposed", "approved", "executed"]
    assert store.pending_approvals() == []


def test_pending_approval_survives_a_restart(stores, pg_schema):
    """アプリのコンテナを作り直しても、承認待ちは DB に残る（Stateless）。新しい接続・新しいインスタンスで再開する。"""
    ticket = start(stores)
    fresh_db = Database(URL, schema=pg_schema)
    store, persistence = PostgresReviewStore(fresh_db), PostgresFlowPersistence(fresh_db)
    assert [p["order_id"] for p in store.pending_approvals()] == [ORDER]
    assert review_flow.resolve_review(ticket.flow_id, "approve", "approver-001", store, persistence) == "registered"
    fresh_db.close()


def test_rejection_registers_nothing(stores):
    store, persistence = stores
    ticket = start(stores)
    assert review_flow.resolve_review(ticket.flow_id, "reject", "approver-001", store, persistence) == "cancelled"
    assert store.list_requests() == [] and [e["event"] for e in store.audit_log(ticket.flow_id)] == ["proposed", "rejected"]


def test_requester_cannot_approve_and_the_request_stays_pending(stores):
    store, persistence = stores
    ticket = start(stores)
    with pytest.raises(review_flow.SelfApprovalError):
        review_flow.resolve_review(ticket.flow_id, "approve", "user-001", store, persistence)
    assert [p["flow_id"] for p in store.pending_approvals()] == [ticket.flow_id] and store.list_requests() == []
    assert "self_approval_blocked" in [e["event"] for e in store.audit_log(ticket.flow_id)]


def test_a_second_decision_is_refused(stores):
    store, persistence = stores
    ticket = start(stores)
    review_flow.resolve_review(ticket.flow_id, "reject", "approver-001", store, persistence)
    with pytest.raises(ValueError):
        review_flow.resolve_review(ticket.flow_id, "approve", "approver-002", store, persistence)
    assert store.list_requests() == []


def test_invalid_order_never_waits_for_approval(stores):
    ticket = start(stores, "O9999")
    assert ticket.status == "invalid" and stores[0].pending_approvals() == []


def test_concurrent_conflicting_decisions_leave_exactly_one_decision(stores):
    """承認と却下が、同時に来ても、決定は1つだけ（二重の登録・矛盾した監査ログを残さない）。"""
    store, persistence = stores
    outcomes = collections.Counter()
    orders = [f"O{i:04d}" for i in range(2, 24) if i not in (2, 3)]  # 要レビューでない注文
    valid = []
    for oid in orders:
        t = start(stores, oid)
        if t.status == "pending_approval":
            valid.append(t)
    assert len(valid) >= 10
    for ticket in valid[:15]:
        results, barrier = {}, threading.Barrier(2)

        def go(name, decision):
            barrier.wait()
            try:
                results[name] = review_flow.resolve_review(ticket.flow_id, decision, name, store, persistence)
            except ValueError:
                results[name] = "refused"

        threads = [threading.Thread(target=go, args=("approver-a", "approve")), threading.Thread(target=go, args=("approver-b", "reject"))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        decisions = [e["event"] for e in store.audit_log(ticket.flow_id) if e["event"] in ("approved", "rejected")]
        outcomes[(len(decisions), sum(r != "refused" for r in results.values()))] += 1
    assert set(outcomes) == {(1, 1)}, outcomes  # 決定が1つ・結果を受け取った人も1人


# --- API（PostgreSQL を使う本物の承認 Flow）---

def test_api_serves_approvals_from_postgresql_and_reports_readiness(stores):
    from fastapi.testclient import TestClient
    store, persistence = stores
    from shared.postgres_sales import PostgresSalesRepository  # noqa: F401  売上が取り込み済みであること
    deps = api.Dependencies(review_store=store, flow_db=persistence, preference_store_factory=lambda: None,
                            readiness_check=lambda: None)
    client = TestClient(api.create_app(deps), raise_server_exceptions=False)
    ticket = start(stores)
    [item] = client.get("/v1/reviews/pending").json()["items"]
    assert item["flow_id"] == ticket.flow_id
    r = client.post(f"/v1/reviews/{ticket.flow_id}/decision", json={"decision": "approve", "approver_id": "approver-001"})
    assert r.status_code == 200 and r.json()["result"] == "registered"
    assert client.get("/readyz").json() == {"status": "ready"}


def test_readiness_fails_without_leaking_details_while_liveness_stays_ok(stores):
    from fastapi.testclient import TestClient

    def down():
        raise ConnectionError("secret-host:5432 password=hunter2")

    deps = api.Dependencies(review_store=stores[0], flow_db=stores[1], preference_store_factory=lambda: None, readiness_check=down)
    client = TestClient(api.create_app(deps), raise_server_exceptions=False)
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"} and "hunter2" not in r.text
    assert client.get("/healthz").status_code == 200  # プロセスは生きている（再起動しても直らない）
