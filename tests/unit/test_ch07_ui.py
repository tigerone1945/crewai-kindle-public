"""第7章の画面テスト（AppTest）。入力Guardrailは本物、分析サービスと Memory は偽物（LLMを呼ばない）。"""
import json

import pytest
from streamlit.testing.v1 import AppTest

import shared.analysis_service as service
import shared.memory_policy as memory_policy
import shared.tracing as tracing
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch07-guardrails" / "app.py")
SECRET = "sk-abcdef123456"


def result():
    return SalesAnalysisResult(
        question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
        metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"],
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    memory = FakeMemory()
    monkeypatch.setattr(memory_policy, "default_preference_store", lambda: memory_policy.PreferenceStore(memory))
    original_save = tracing.RunTrace.save
    monkeypatch.setattr(tracing.RunTrace, "save", lambda self, directory=tmp_path: original_save(self, directory))
    calls = []

    def fake_run(question, history=(), preferences=()):
        calls.append(question)
        return result()

    monkeypatch.setattr(service, "run_analysis", fake_run)
    return type("Env", (), {"memory": memory, "calls": calls, "traces": tmp_path})()


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    return at


def all_text(at):
    return " ".join(e.value for e in [*at.markdown, *at.warning, *at.error, *at.info, *at.success, *at.caption])


def test_in_scope_question_reaches_the_service(env):
    at = ask(start(), "地域別の売上を比較してください。")
    assert env.calls == ["地域別の売上を比較してください。"] and "東北が最も高い売上です" in all_text(at)


def test_out_of_scope_question_is_stopped_before_service_and_memory(env):
    at = ask(start(), "Pythonでシューティングゲームを作ってください。")
    assert env.calls == [] and env.memory.recall_queries == []
    assert "対応できません" in all_text(at) and not at.exception and not at.error


def test_rejected_question_is_not_kept_on_screen_or_in_the_trace(env):
    at = ask(start(), "Pythonでシューティングゲームを作ってください。")
    assert "シューティング" not in all_text(at)
    saved = json.loads(next(env.traces.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["summary"]["status"] == "rejected" and saved["events"][0]["rule"] == "out_of_scope"
    assert "シューティング" not in json.dumps(saved, ensure_ascii=False)


def test_secret_in_question_never_reaches_service_memory_screen_or_trace(env):
    at = ask(start(), f"API key: {SECRET} で売上を教えて")
    assert env.calls == [] and env.memory.recall_queries == []
    assert "秘密情報" in all_text(at) and SECRET not in all_text(at)
    assert SECRET not in next(env.traces.glob("*.json")).read_text(encoding="utf-8")


def test_follow_up_is_accepted_only_after_a_conversation_exists(env):
    at = start()
    ask(at, "では一番高かったのは？")
    assert env.calls == []
    ask(at, "地域別の売上を比較してください。")
    ask(at, "では一番高かったのは？")
    assert env.calls[-1] == "では一番高かったのは？"


def test_empty_and_too_long_questions_are_rejected_with_guidance(env):
    at = start()
    ask(at, "   ")
    assert "入力してください" in all_text(at)
    ask(at, "売上" * 300)
    assert "500文字以内" in all_text(at) and env.calls == []


def test_output_guardrail_failure_shows_generic_message_without_details(env, monkeypatch):
    def fail(*a, **k):
        raise service.AnalysisError("3回試しましたが検証を通りませんでした: 文中の 60% は…")

    monkeypatch.setattr(service, "run_analysis", fail)
    at = ask(start(), "地域別の売上を比較してください。")
    assert "質問の表現を変えて" in all_text(at) and "60%" not in all_text(at)
