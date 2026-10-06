"""承認Flow（第8章）のテスト。本物のFlow・SQLiteを使い、LLMは呼ばない。"""
import pytest

import shared.review_flow as rf
from shared.review_requests import InvalidReviewTarget, ReviewStore, validate_review_target
from shared.sales_db import SalesRepository
from shared.tracing import trace_run


@pytest.fixture
def env(tmp_path):
    return type("Env", (), {"db": tmp_path / "review.db", "flows": tmp_path / "flows.db",
                            "store": ReviewStore(tmp_path / "review.db")})()


def start(env, order_id="O0001", by="user-001"):
    return rf.start_review(order_id, by, env.store, env.flows)


def resolve(env, ticket, decision="approve", by="approver-1", store=None):
    return rf.resolve_review(ticket.flow_id, decision, by, store or env.store, env.flows)


def events(env, flow_id=None):
    return [e["event"] for e in env.store.audit_log(flow_id)]


# --- 対象の検査 ---

def test_valid_target_returns_facts_without_personal_names():
    facts = validate_review_target("O0001")
    assert facts["order_id"] == "O0001" and facts["sales_amount"] == 264600
    assert "customer_name" not in facts and "customer_id" not in facts


@pytest.mark.parametrize("order_id,code", [("o0001", "bad_format"), ("O1", "bad_format"), ("'; DROP TABLE x;--", "bad_format"), ("O9999", "not_found")])
def test_invalid_targets_are_rejected_with_a_code(order_id, code):
    with pytest.raises(InvalidReviewTarget) as e:
        validate_review_target(order_id)
    assert e.value.code == code


def test_already_flagged_order_is_not_eligible():
    df = SalesRepository.default().to_dataframe()
    flagged = df[df["review_flag"] == "要レビュー"].iloc[0]["order_id"]
    with pytest.raises(InvalidReviewTarget) as e:
        validate_review_target(flagged)
    assert e.value.code == "already_flagged"


# --- 承認と却下 ---

def test_nothing_is_written_before_approval(env):
    ticket = start(env)
    assert ticket.status == "pending_approval" and ticket.facts["order_id"] == "O0001"
    assert env.store.list_requests() == []  # BR-06: 承認の前に Side Effect は起きない
    assert events(env) == ["proposed"] and env.store.pending_approvals()[0]["order_id"] == "O0001"


def test_approval_registers_the_request_and_audits_who_decided(env):
    ticket = start(env)
    assert resolve(env, ticket, "approve", "approver-1") == "registered"
    [row] = env.store.list_requests()
    assert (row["order_id"], row["requested_by"], row["approved_by"]) == ("O0001", "user-001", "approver-1")
    assert events(env) == ["proposed", "approved", "executed"]
    assert [e["actor"] for e in env.store.audit_log()] == ["user-001", "approver-1", "approver-1"]
    assert env.store.pending_approvals() == []


def test_rejection_does_not_register_anything(env):
    ticket = start(env)
    assert resolve(env, ticket, "reject") == "cancelled"
    assert env.store.list_requests() == [] and events(env) == ["proposed", "rejected"]
    assert env.store.pending_approvals() == []


def test_resume_works_from_a_fresh_process(env):
    """アプリを再起動しても、承認待ちを別のインスタンスで再開できる（Store も Flow も作り直す）。"""
    ticket = start(env)
    fresh_store = ReviewStore(env.db)
    assert fresh_store.pending_approvals()[0]["flow_id"] == ticket.flow_id
    assert resolve(env, ticket, "approve", store=fresh_store) == "registered"
    assert len(ReviewStore(env.db).list_requests()) == 1


@pytest.mark.parametrize("reply", ["", "はい", "approved", "approve if the amount is small", "APPROVE", "approve now"])
def test_only_the_exact_word_approves(env, reply):
    """承認の判断は完全一致だけ。条件つき・曖昧・言い換えは、すべて却下（Spike s15）。LLMも呼ばれない。"""
    ticket = start(env)
    flow = rf.ReviewFlow.from_pending(ticket.flow_id, rf._persistence(env.flows), store=env.store)
    flow.state.approved_by = "approver-1"
    with trace_run() as trace:
        flow.resume(reply)
    assert flow.state.result == "cancelled" and env.store.list_requests() == []
    assert trace.summary()["llm_calls"] == 0


def test_invalid_decision_value_is_refused_without_touching_the_flow(env):
    ticket = start(env)
    with pytest.raises(ValueError, match="decision"):
        resolve(env, ticket, "yes")
    assert env.store.pending_approvals()  # まだ承認待ちのまま


# --- 職務の分離・二重実行・再確認 ---

def test_requester_cannot_approve_own_request_and_it_stays_pending(env):
    ticket = start(env, by="user-001")
    with pytest.raises(rf.SelfApprovalError):
        resolve(env, ticket, "approve", by="user-001")
    assert env.store.list_requests() == [] and "self_approval_blocked" in events(env)
    assert resolve(env, ticket, "approve", by="approver-1") == "registered"  # 別の承認者なら通る


def test_a_decided_flow_cannot_be_resumed_again(env):
    ticket = start(env)
    resolve(env, ticket, "approve")
    with pytest.raises(ValueError, match="No pending feedback"):
        resolve(env, ticket, "approve")
    assert len(env.store.list_requests()) == 1


def test_duplicate_request_is_blocked_by_the_database(env):
    first, second = start(env, by="user-001"), start(env, by="user-002")
    assert resolve(env, first, "approve") == "registered"
    assert resolve(env, second, "approve") == "duplicate"
    assert len(env.store.list_requests()) == 1 and "execution_failed" in events(env, second.flow_id)


def test_preconditions_are_checked_again_right_before_execution(env, monkeypatch):
    ticket = start(env)
    monkeypatch.setattr(rf, "validate_review_target", lambda order_id: (_ for _ in ()).throw(InvalidReviewTarget("already_flagged", "x")))
    assert resolve(env, ticket, "approve") == "not_eligible"
    assert env.store.list_requests() == []


def test_invalid_target_never_waits_for_approval(env):
    ticket = start(env, "O9999")
    assert ticket.status == "invalid" and "見つかりません" in ticket.message
    assert env.store.pending_approvals() == [] and events(env) == ["invalid"]


@pytest.mark.parametrize("actor", ["../etc", "", "a b"])
def test_actor_ids_are_validated(env, actor):
    with pytest.raises(ValueError):
        start(env, by=actor)
    ticket = start(env)
    with pytest.raises(ValueError):
        resolve(env, ticket, "approve", by=actor)


def test_audit_log_holds_only_ids_and_codes(env):
    ticket = start(env)
    resolve(env, ticket, "approve")
    for e in env.store.audit_log():
        assert set(e) == {"at", "flow_id", "event", "order_id", "actor", "detail"}
        assert e["detail"] == ""  # 自由記述を持たない。質問文や顧客名は入らない


def test_flow_steps_are_visible_in_the_trace(env):
    with trace_run() as trace:
        ticket = start(env)
        resolve(env, ticket, "approve")
    steps = [(e.name, e.status) for e in trace.events if e.kind == "flow"]
    assert ("request_approval", "paused") in steps and ("execute", "ok") in steps
    assert trace.summary()["llm_calls"] == 0


# --- 依頼の判定 ---

@pytest.mark.parametrize("question,expected", [
    ("O0001をレビュー対象として登録してください。", ["O0001"]),
    ("注文O0042のレビュー依頼を出してください", ["O0042"]),
    ("O0001とO0002をレビュー対象に追加して", ["O0001", "O0002"]),
    ("O0001の売上を教えてください", []),
    ("要レビューの注文は何件ですか？", []),
    ("レビュー対象として登録してください", []),
    ("XO00012をレビュー対象として登録して", []),
])
def test_review_request_detection(question, expected):
    assert rf.extract_review_order_ids(question) == expected
