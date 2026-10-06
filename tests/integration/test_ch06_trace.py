"""LLMを使う統合テスト（APIキーが必要）。本物のCrew実行がTraceに記録され、機微な内容が残らないことを確かめる。

記録される件数（LLM呼び出し2回、Tool1回）はモデルの振る舞いに依存するため、下限と包含関係で判定する。
"""
import os

import pytest

import shared.analysis_service as service
from shared.tracing import trace_run
from tests.helpers import ROOT, analyze_with_retry

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
QUESTION = "地域別の売上を比較してください。"


@pytest.fixture(scope="module")
def traced():
    with trace_run() as trace:
        analyze_with_retry(service, QUESTION)
    return trace


def test_agent_task_tool_and_llm_calls_are_all_recorded(traced):
    kinds = {e.kind for e in traced.events}
    assert {"crew", "task", "agent", "llm", "tool", "attempt"} <= kinds
    assert "get_sales_by_region" in traced.summary()["tool_calls"]
    assert next(e for e in traced.events if e.kind == "task").name == "sales_analysis"


def test_metrics_are_measured(traced):
    s = traced.summary()
    assert s["llm_calls"] >= 2 and s["prompt_tokens"] > 0 and s["completion_tokens"] > 0
    assert s["duration_ms"] and s["duration_ms"] > 0
    tool = next(e for e in traced.events if e.kind == "tool")
    assert tool.duration_ms is not None and tool.duration_ms < s["duration_ms"]


def test_trace_does_not_contain_the_question_or_tool_output(traced):
    dumped = traced.to_json()
    assert QUESTION not in dumped and "8439765" not in dumped and "8,439,765" not in dumped
