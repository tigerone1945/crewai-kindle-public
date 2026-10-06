"""LLMを実際に呼ぶ統合テスト（APIキーが必要）。Schema適合と、数値が実データと一致することを確認する。"""
import json
import os

import pytest

from shared.tool_calls import recorder
from tests.helpers import ROOT, load_chapter_module

main = load_chapter_module("ch03-structured-output", "main.py")
CASES = {c["id"]: c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]}

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)


def test_region_analysis_is_structured_and_grounded():
    recorder.clear()
    r = main.run_analysis(CASES["case-02-region-comparison"]["turns"][0])
    assert r.analysis_type == "region_sales"
    assert "get_sales_by_region" in recorder.calls
    values = {m.value for m in r.metrics}
    assert {8439765, 8373825, 4980410, 3268525, 3236170} <= values


def test_overall_analysis_does_not_use_total_amount():
    r = main.run_analysis(CASES["case-01-overall-sales"]["turns"][0])
    assert r.analysis_type == "sales_summary"
    assert 28298695 in {m.value for m in r.metrics}
    assert 30500770 not in {m.value for m in r.metrics}


def test_unsupported_question_returns_unsupported():
    r = main.run_analysis("来月の売上を予測してください。")
    assert r.analysis_type == "unsupported" and r.metrics == []
