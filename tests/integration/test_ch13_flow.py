"""LLMを使う統合テスト（APIキーが必要）。第13章: Flow の経路を、Trace（実際に起きたこと）で確かめる。

出力は確率的。ここで確かめるのは、経路の選択（コードで決まる）と、LLM・Tool が呼ばれたかどうか。
- 範囲外（Case 04）と状態を変える依頼（Case 05）は、LLM を一度も呼ばない。
- 統合分析（Case 07）は3つの Role、通常の分析は単一の Agent が動く。
"""
import os

import pytest

from shared.analysis_flow import run_question
from shared.review_requests import ReviewStore
from shared.roles import ROLE_NAMES, boundary_violations, role_keys
from shared.tracing import trace_run
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)


@pytest.fixture
def env(tmp_path):
    return type("Env", (), {"store": ReviewStore(tmp_path / "review.db"), "flow_db": tmp_path / "flows.db"})()


def ask(env, question):
    for _ in range(2):  # まれな失敗（Guardrail の使い切り）は、全体を1回だけ再実行して吸収する（失敗率は s21 で測定）
        with trace_run() as trace:
            outcome = run_question(question, "user-001", review_store=env.store, flow_db=env.flow_db)
        if outcome.kind != "escalated":
            break
    return outcome, trace.summary()


def test_out_of_scope_is_stopped_without_calling_the_llm(env):
    outcome, summary = ask(env, "Pythonでゲームを作ってください。")
    assert (outcome.kind, outcome.route) == ("rejected", "rejected")
    assert summary["llm_calls"] == 0 and summary["tool_calls"] == [] and summary["roles"] == []


def test_review_request_waits_for_approval_without_calling_the_llm(env):
    outcome, summary = ask(env, "O0001をレビュー対象として登録してください。")
    assert (outcome.kind, outcome.route) == ("review_pending", "review_request")
    assert summary["llm_calls"] == 0 and summary["roles"] == []  # Agent は関与しない（BR-05, BR-06）
    assert env.store.list_requests() == [] and len(env.store.pending_approvals()) == 1


def test_plain_analysis_runs_the_single_agent(env):
    outcome, summary = ask(env, "地域別の売上を比較してください。")
    assert (outcome.kind, outcome.route) == ("analysis", "single")
    assert summary["llm_calls"] >= 1 and "get_sales_by_region" in summary["tool_calls"]
    assert len(summary["roles"]) == 1  # 単一の Agent（3つの Role ではない）


def test_quality_question_runs_the_three_roles_through_the_flow(env):
    outcome, summary = ask(env, "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
    assert (outcome.kind, outcome.route) == ("analysis", "integrated")
    assert set(role_keys(summary["roles"])) == set(ROLE_NAMES)
    assert boundary_violations(summary["tool_roles"]) == []
    assert outcome.result.analysis_type == "integrated_analysis"
