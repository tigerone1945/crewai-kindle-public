"""LLMを使う統合テスト（APIキーが必要）。第12章: Process の違い（sequential / hierarchical）を、Trace で確かめる。

出力は確率的。ここで確かめるのは、実測（Spike s20、各6回）で毎回成り立った性質だけ。
- sequential: 担当と順序が、Task の定義のとおりになる。委譲は起きない。
- hierarchical: Manager が委譲で仕事を進める。順序と回数は毎回変わるため、固定しない。どちらでも、権限の境界は破れない。
"""
import os

import pytest

import shared.analysis_service as service
from shared.roles import ROLE_NAMES, boundary_violations, delegation_count, role_keys
from shared.tracing import trace_run
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
QUESTION = "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。"


def run(process):
    last = None
    for _ in range(2):  # まれな失敗は全体を1回だけ再実行して吸収する（失敗率は s20 で測定）
        with trace_run() as trace:
            try:
                result = service.run_multi_role_analysis(QUESTION, process=process)
            except service.AnalysisError as e:
                last = e
                continue
        return result, trace.summary()
    raise last


@pytest.fixture(scope="module")
def sequential():
    return run("sequential")


@pytest.fixture(scope="module")
def hierarchical():
    return run("hierarchical")


def test_sequential_runs_each_role_once_in_the_order_the_tasks_define(sequential):
    _, summary = sequential
    assert role_keys(summary["roles"]) == ["data_quality", "sales_analyst", "business_insight"]


def test_sequential_never_delegates_and_keeps_the_tool_boundary(sequential):
    _, summary = sequential
    assert delegation_count(summary["tool_calls"]) == 0
    assert boundary_violations(summary["tool_roles"]) == []


def test_hierarchical_manager_delegates_to_all_three_roles(hierarchical):
    result, summary = hierarchical
    assert delegation_count(summary["tool_calls"]) >= 3
    assert set(role_keys(summary["roles"])) == set(ROLE_NAMES)
    assert result.analysis_type == "integrated_analysis"


def test_hierarchical_keeps_the_tool_boundary(hierarchical):
    """Manager 自身は Tool を持たない。データの Tool は、割り当てられた Role だけが実行する。"""
    _, summary = hierarchical
    assert boundary_violations(summary["tool_roles"]) == []
    assert summary["status"] == "ok"  # 最終の出力は、sequential と同じ Guardrail を通っている
