"""第5章の画面テスト（AppTest。分析サービスとMemoryは偽物に差し替えるため、LLMを呼ばない）。"""
import pytest
from streamlit.testing.v1 import AppTest

import shared.analysis_service as service
import shared.memory_policy as memory_policy
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch05-memory" / "app.py")


def result(summary="東北が最も高い売上です"):
    return SalesAnalysisResult(
        question="q", analysis_type="region_sales", summary=summary,
        metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"],
    )


@pytest.fixture
def env(monkeypatch):
    memory = FakeMemory()
    monkeypatch.setattr(memory_policy, "default_preference_store", lambda: memory_policy.PreferenceStore(memory))
    calls = []

    def fake_run(question, history=(), preferences=()):
        calls.append({"question": question, "history": list(history), "preferences": list(preferences)})
        return result(f"回答{len(calls)}")

    monkeypatch.setattr(service, "run_analysis", fake_run)
    return type("Env", (), {"memory": memory, "calls": calls})()


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    return at


def all_text(at):
    return " ".join(e.value for e in [*at.markdown, *at.warning, *at.error, *at.info, *at.success, *at.caption])


def test_initial_render(env):
    at = start()
    assert not at.exception and at.title[0].value == "AI売上分析アシスタント"
    assert "まだありません" in all_text(at)


def test_follow_up_receives_the_previous_turn_as_context(env):
    at = start()
    ask(at, "地域別の売上を分析してください。")
    ask(at, "では一番高かった地域は？")
    assert env.calls[0]["history"] == []
    h = env.calls[1]["history"]
    assert len(h) == 1 and h[0].question == "地域別の売上を分析してください。" and h[0].analysis_type == "region_sales"


def test_previous_results_stay_on_screen(env):
    at = start()
    ask(at, "地域別の売上を分析してください。")
    ask(at, "では一番高かった地域は？")
    text = all_text(at)
    assert "回答1" in text and "回答2" in text


def test_new_conversation_clears_context(env):
    at = start()
    ask(at, "地域別の売上を分析してください。")
    at.button[-1].click().run()  # サイドバー最後のボタン＝「新しい会話を始める」
    ask(at, "カテゴリ別は？")
    assert env.calls[-1]["history"] == []


def test_registered_preference_is_passed_and_shown(env):
    at = start()
    at.text_input[1].set_value("要約は必ず「結論:」で始める")  # [0]=利用者ID, [1]=好み
    at.button[0].click().run()
    assert "登録しました" in all_text(at) and "「結論:」" in all_text(at)
    ask(at, "地域別の売上を分析してください。")
    assert env.calls[0]["preferences"] == ["要約は必ず「結論:」で始める"]
    assert "適用した好み" in all_text(at)


def test_business_facts_cannot_be_registered(env):
    at = start()
    at.text_input[1].set_value("東北の売上は8,439,765円")
    at.button[0].click().run()
    assert "登録できません" in all_text(at) and "数値" in all_text(at)
    assert env.memory.records == []


def test_preference_can_be_deleted(env):
    at = start()
    at.text_input[1].set_value("表形式で見たい")
    at.button[0].click().run()
    delete = [b for b in at.button if b.label == "削除"][0]
    delete.click().run()
    assert env.memory.records == [] and "まだありません" in all_text(at)


def test_other_users_preferences_are_not_used(env):
    at = start()
    at.text_input[1].set_value("表形式で見たい")
    at.button[0].click().run()
    at.text_input[0].set_value("user-002").run()
    ask(at, "地域別の売上を分析してください。")
    assert env.calls[0]["preferences"] == []


def test_invalid_user_id_stops_the_app_with_an_error(env):
    at = start()
    at.text_input[0].set_value("../etc").run()
    assert at.error and len(at.chat_input) == 0


def test_validation_failure_and_unexpected_error_do_not_leak_details(env, monkeypatch):
    def reject(*a, **k):
        raise service.AnalysisError("文中の 60% は実データから…")

    monkeypatch.setattr(service, "run_analysis", reject)
    at = start()
    ask(at, "地域別の売上を分析してください。")
    assert "質問の表現を変えて" in all_text(at) and "60%" not in all_text(at)

    def boom(*a, **k):
        raise ConnectionError("secret-host:5432")

    monkeypatch.setattr(service, "run_analysis", boom)
    ask(at, "もう一度")
    assert "エラーが発生しました" in all_text(at) and "secret-host" not in all_text(at)
    assert env.memory.records == []  # 失敗した質問はMemoryへ保存されない


def test_memory_outage_does_not_stop_analysis(env, monkeypatch):
    env.memory.fail_recall = True
    at = start()
    ask(at, "地域別の売上を分析してください。")
    assert not at.exception and "回答1" in all_text(at) and env.calls[0]["preferences"] == []


def test_input_rejected_by_guardrail_shows_its_reason_not_a_generic_error(env, monkeypatch):
    def reject(*a, **k):
        raise service.InputRejected("out_of_scope", "この質問には対応できません。売上についての質問を入力してください。")

    monkeypatch.setattr(service, "run_analysis", reject)
    at = ask(start(), "Pythonでゲームを作ってください。")
    assert "売上についての質問" in all_text(at) and "エラーが発生しました" not in all_text(at) and not at.exception
