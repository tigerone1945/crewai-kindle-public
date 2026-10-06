"""LLMを実際に呼ぶ統合テスト（OPENAI_API_KEY が必要）。共通 Evaluation Case 01 / 02 を検証する。"""
import json

import pytest

from evaluations.checks import check_expected
from shared.tool_calls import recorder
from tests.helpers import ROOT, load_chapter_module

main = load_chapter_module("ch02-tools", "main.py")
CASES = {c["id"]: c for c in json.loads((ROOT / "evaluations" / "evaluation_cases.json").read_text(encoding="utf-8"))["cases"]}

pytestmark = pytest.mark.skipif(
    not __import__("os").getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(),
    reason="LLMを呼ぶためAPIキーが必要",
)


@pytest.mark.parametrize("case_id", ["case-01-overall-sales", "case-02-region-comparison"])
def test_evaluation_case(case_id):
    case = CASES[case_id]
    recorder.clear()
    answer = main.build_crew(case["turns"][0]).kickoff().raw
    assert check_expected(case["expected"], answer, recorder.calls) == [], answer
