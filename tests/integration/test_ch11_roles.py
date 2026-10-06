"""LLMを使う統合テスト（APIキーが必要）。Evaluation Case 07（Multi-Agent）: 3つの Role が、それぞれの責務だけを実行する。

出力は確率的。Role の呼び出し・Tool の権限境界は、Trace（実際に実行されたこと）で判定する。委譲の記録だけでは判定しない（Spike s09）。
"""
import json
import os

import pytest

import shared.analysis_service as service
from evaluations.checks import check_expected
from shared.roles import ROLE_NAMES, boundary_violations, role_keys
from shared.tracing import trace_run
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
CASE = next(c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]
            if c["id"] == "case-07-multi-agent")


@pytest.fixture(scope="module")
def outcome():
    last = None
    for _ in range(2):  # LLM が絡むため、まれな失敗は全体を1回だけ再実行して吸収する（サービスの失敗率は s19 で測定）
        with trace_run() as trace:
            try:
                result = service.run_multi_role_analysis(CASE["turns"][0])
            except service.AnalysisError as e:
                last = e
                continue
        return result, trace.summary()
    raise last


def answer_text(result):
    return " ".join([result.summary, *result.insights, *result.recommendations, *(f"{m.label} {m.value:,.0f}" for m in result.metrics)])


def test_case_07_all_three_roles_ran_and_the_total_is_present(outcome):
    result, summary = outcome
    failures = check_expected(CASE["expected"], answer_text(result), summary["tool_calls"], role_keys(summary["roles"]))
    assert failures == [], failures
    assert result.analysis_type == "integrated_analysis"


def test_each_tool_was_executed_only_by_its_own_role(outcome):
    _, summary = outcome
    assert summary["tool_roles"], "どの Role が Tool を実行したかが記録されていない"
    assert boundary_violations(summary["tool_roles"]) == []
    executed_by = {role for role, tool in summary["tool_roles"] if tool == "check_data_quality"}
    assert executed_by == {ROLE_NAMES["data_quality"]}


def test_the_insight_role_ran_without_any_tool(outcome):
    _, summary = outcome
    assert ROLE_NAMES["business_insight"] in summary["roles"]
    assert ROLE_NAMES["business_insight"] not in {role for role, _ in summary["tool_roles"]}


def test_the_result_passed_the_same_number_validation_as_the_single_agent(outcome):
    """insight_must_not_introduce_new_numbers: 出力のGuardrail（実データとの突き合わせ）を通っている。"""
    _, summary = outcome
    assert summary["status"] == "ok" and summary["attempts"] >= 1
