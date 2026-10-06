"""LLMを使う統合テスト（APIキーが必要）。実Crewで、入力Guardrail・Task Guardrailが設計どおりに働くことを確かめる。

偽物のCrewでは検出できなかった不具合（Spike s14）の再発を防ぐテストを含む:
- Guardrail に __call__ を持つインスタンスを渡すと、CrewAI の inspect.getsource() が TypeError になる
- Guardrail 付きだと、CrewOutput.pydantic が空になることがある
実測（gpt-4o-mini, 通常質問10回）: 1回で合格8回、再生成2回、使い切り0回。
"""
import json
import os

import pytest

import shared.analysis_service as service
from shared.guardrails import InputRejected
from shared.tracing import trace_run
from tests.helpers import ROOT, analyze_with_retry

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
CASES = {c["id"]: c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]}


def test_case_04_out_of_scope_is_refused_without_calling_llm_or_tools():
    case = CASES["case-04-out-of-scope"]
    assert case["expected"]["must_be_refused"] and case["expected"]["tools_forbidden"] == ["*"]
    with trace_run() as trace:
        with pytest.raises(InputRejected) as e:
            service.run_analysis(case["turns"][0])
    s = trace.summary()
    assert e.value.code == "out_of_scope"
    assert s["llm_calls"] == 0 and s["tool_calls"] == [] and s["prompt_tokens"] == 0  # 拒否は、Crewの外で決まる


def test_in_scope_question_passes_both_guardrails_and_returns_a_checked_result():
    with trace_run() as trace:
        result = analyze_with_retry(service, "地域別の売上を比較してください。")
    assert result.analysis_type == "region_sales"
    assert any(e.kind == "attempt" and e.status == "ok" for e in trace.events)  # Task Guardrail が実際に呼ばれた


def test_crewai_exhaustion_exception_still_starts_with_the_prefix_we_rely_on():
    """AnalysisError への変換は、CrewAI の Exception の書き出しに依存している。CrewAI を更新したときに気づくための固定テスト。"""
    crew = service.build_crew("地域別の売上を比較してください。", guardrail=_always_reject, guardrail_max_retries=1)
    with pytest.raises(Exception) as e:
        crew.kickoff()
    assert str(e.value).startswith("Task failed guardrail validation")


def _always_reject(output):
    return False, "テスト用: 常に却下します"
