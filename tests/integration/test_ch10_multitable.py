"""LLMを使う統合テスト（APIキーが必要）。Evaluation Case 06（Multi-table）: 結合を伴う集計を、許可された Tool だけで行う。"""
import json
import os

import pytest

import shared.analysis_service as service
from evaluations.checks import check_expected
from shared.tracing import trace_run
from tests.helpers import ROOT, analyze_with_retry

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
CASE = next(c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]
            if c["id"] == "case-06-multi-table")


def run_case():
    with trace_run() as trace:
        result = analyze_with_retry(service, CASE["turns"][0])
    return result, trace.summary()["tool_calls"]


def test_case_06_uses_the_allowed_join_tool_and_the_correct_numbers():
    result, tools = run_case()
    answer = " ".join([result.summary, *result.insights, *(f"{m.label} {m.value:,.0f}" for m in result.metrics)])
    failures = check_expected(CASE["expected"], answer, tools)
    assert failures == [], failures
    assert result.analysis_type == "customer_type_sales"


def test_no_arbitrary_sql_tool_exists_or_is_called():
    _, tools = run_case()
    assert not any("sql" in t.lower() for t in tools)
