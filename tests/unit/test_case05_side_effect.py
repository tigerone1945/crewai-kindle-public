"""Evaluation Case 05（Side Effect + Approval）を、評価ケースの定義から駆動して検証する（LLMを使わない）。"""
import json

import pytest

import shared.review_flow as rf
from shared.review_requests import ReviewStore
from shared.sales_tools import SALES_TOOLS
from tests.helpers import ROOT

CASE = next(c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]
            if c["id"] == "case-05-side-effect-approval")


@pytest.fixture
def env(tmp_path):
    return type("Env", (), {"store": ReviewStore(tmp_path / "r.db"), "flows": tmp_path / "f.db"})()


@pytest.mark.parametrize("scenario", CASE["scenarios"], ids=lambda s: s["id"])
def test_side_effect_runs_only_when_approved(env, scenario):
    assert rf.extract_review_order_ids(CASE["turns"][0]) == ["O0001"]
    ticket = rf.start_review("O0001", "user-001", env.store, env.flows)
    assert env.store.list_requests() == []  # requires_approval_before: 承認の前には実行されない
    decision = rf.APPROVE if scenario["human_decision"] == "approve" else rf.REJECT
    rf.resolve_review(ticket.flow_id, decision, "approver-1", env.store, env.flows)
    assert bool(env.store.list_requests()) is scenario["expect_side_effect"]


def test_approval_is_recorded_before_the_side_effect(env):
    ticket = rf.start_review("O0001", "user-001", env.store, env.flows)
    rf.resolve_review(ticket.flow_id, rf.APPROVE, "approver-1", env.store, env.flows)
    order = [e["event"] for e in env.store.audit_log()]
    assert order.index("proposed") < order.index("approved") < order.index("executed")


def test_the_agent_is_never_given_a_write_tool():
    """BR-05: Agent に渡す Tool は読み取り専用だけ。登録（High Risk）は Flow が承認後に実行する。"""
    names = {t.name for t in SALES_TOOLS}
    assert names == {"get_sales_summary", "get_sales_by_category", "get_sales_by_region"}
    assert not any("review" in n or "create" in n or "register" in n for n in names)
