"""LLMと本物のMemoryを使う統合テスト（APIキーが必要）。CrewAI固有 Case c01 / c02 と、共通 Case 03 を検証する。

出力は確率的。実測（gpt-4o-mini, 各5回）: 好みあり 5/5・なし 0/5、Follow-up 5/5。
"""
import json
import os
import re

import pytest
from crewai.memory.unified_memory import Memory

import shared.analysis_service as service
from evaluations.checks import check_expected
from shared.conversation import Turn
from shared.memory_policy import PolicyViolation, PreferenceStore
from shared.sales_data import MODEL
from tests.helpers import ROOT, analyze_with_retry

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)
EVAL = ROOT / "evaluations"
CASES = {c["id"]: c for f in ("evaluation_cases.json", "crewai_specific_cases.json")
         for c in json.loads((EVAL / f).read_text(encoding="utf-8"))["cases"]}
QUESTION = "地域別の売上を分析してください。"


@pytest.fixture
def store(tmp_path):
    memory = Memory(llm=MODEL, storage=str(tmp_path / "memory"))
    yield PreferenceStore(memory)
    memory.close()


def test_c01_preference_changes_the_answer(store):
    case = CASES["case-c01-memory-preference"]
    seed = case["setup"]["memory_seed"]
    store.remember_preference("user-001", seed["content"])
    prefs = store.recall_preferences("user-001", QUESTION)
    assert prefs == [seed["content"]]

    with_pref = analyze_with_retry(service, QUESTION, preferences=prefs)
    assert check_expected({"answer_starts_with": case["expected"]["answer_starts_with"]}, with_pref.summary, []) == []
    without = analyze_with_retry(service, QUESTION)
    assert not without.summary.startswith("結論")  # 対照: 好みがなければ反映されない


def test_c02_no_business_facts_in_memory(store):
    store.remember_preference("user-001", "要約は必ず「結論:」で始める")
    with pytest.raises(PolicyViolation):
        store.remember_preference("user-001", "東北の売上は8,439,765円")
    result = analyze_with_retry(service, QUESTION, preferences=store.recall_preferences("user-001", QUESTION))
    assert result.metrics  # 分析自体は成功している
    contents = [content for _, content in store.list_preferences("user-001")]
    assert contents == ["要約は必ず「結論:」で始める"]
    assert not any(re.search(r"\d{1,3}(,\d{3})+", c) or "円" in c for c in contents)


def test_case03_follow_up_uses_conversation_context():
    first = analyze_with_retry(service, QUESTION)
    history = [Turn.from_result(QUESTION, first)]
    r = analyze_with_retry(service, "では一番高かった地域は？", history=history)
    text = r.summary + " ".join(r.insights)
    assert "東北" in text and "関東が最も高い" not in text


def test_deleting_all_preferences(store):
    store.remember_preference("user-001", "結果は簡潔に")
    assert store.delete_all("user-001") >= 1
    assert store.list_preferences("user-001") == []
    assert store.recall_preferences("user-001", "形式") == []
