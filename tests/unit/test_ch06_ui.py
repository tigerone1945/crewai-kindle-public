"""第6章の画面テスト（AppTest）。分析サービスは偽物だが、Eventは本物のEvent Busへ流すため、Traceの経路は本物。"""
import datetime
import json
import logging

import pytest
from crewai.events import crewai_event_bus
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent
from streamlit.testing.v1 import AppTest

import shared.analysis_service as service
import shared.memory_policy as memory_policy
import shared.tracing as tracing
from shared.schemas import Metric, SalesAnalysisResult
from shared.tracing import record_app_event
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch06-execution-trace" / "app.py")


def result():
    return SalesAnalysisResult(
        question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
        metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"],
    )


def emit_tool_call():
    now = datetime.datetime.now()
    crewai_event_bus.emit(None, ToolUsageFinishedEvent(
        tool_name="get_sales_by_region", tool_args={}, tool_class="T", started_at=now,
        finished_at=now + datetime.timedelta(milliseconds=30), output="東北 8,439,765円", from_cache=False))


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_policy, "default_preference_store", lambda: memory_policy.PreferenceStore(FakeMemory()))
    original_save = tracing.RunTrace.save
    monkeypatch.setattr(tracing.RunTrace, "save", lambda self, directory=tmp_path: original_save(self, directory))

    def fake_run(question, history=(), preferences=()):
        emit_tool_call()
        record_app_event("attempt", "analysis", attempt=1)
        return result()

    monkeypatch.setattr(service, "run_analysis", fake_run)
    return type("Env", (), {"traces": tmp_path})()


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    return at


def all_text(at):
    return " ".join(e.value for e in [*at.markdown, *at.warning, *at.error, *at.info, *at.success, *at.caption])


def test_trace_is_shown_under_the_answer(env):
    at = ask(start(), "地域別の売上を分析してください。")
    assert not at.exception
    assert any("実行の記録（Trace）" in e.label for e in at.expander)
    assert "呼ばれたTool: get_sales_by_region" in all_text(at)


def test_trace_is_saved_without_question_or_tool_output(env):
    ask(start(), "地域別の売上を分析してください。")
    files = list(env.traces.glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["summary"]["tool_calls"] == ["get_sales_by_region"]
    assert "地域別の売上を分析" not in text and "8,439,765" not in text


def test_failure_keeps_the_trace_and_logs_the_run_id(env, monkeypatch, caplog):
    def reject(*a, **k):
        emit_tool_call()
        raise service.AnalysisError("検証に失敗")

    monkeypatch.setattr(service, "run_analysis", reject)
    with caplog.at_level(logging.ERROR, logger="sales_ui"):
        at = ask(start(), "地域別の売上を分析してください。")
    assert "質問の表現を変えて" in all_text(at)
    run_id = json.loads(next(env.traces.glob("*.json")).read_text(encoding="utf-8"))["summary"]["run_id"]
    assert f"run_id={run_id}" in caplog.text  # Application Log から Trace を探せる
    assert any("実行の記録（Trace）" in e.label for e in at.expander)


def test_trace_save_failure_does_not_stop_the_answer(env, monkeypatch):
    def broken(self, directory=None):
        raise OSError("disk full")

    monkeypatch.setattr(tracing.RunTrace, "save", broken)
    at = ask(start(), "地域別の売上を分析してください。")
    assert not at.exception and "東北が最も高い売上です" in all_text(at)


def test_input_rejected_by_guardrail_shows_its_reason_not_a_generic_error(env, monkeypatch):
    def reject(*a, **k):
        raise service.InputRejected("out_of_scope", "この質問には対応できません。売上についての質問を入力してください。")

    monkeypatch.setattr(service, "run_analysis", reject)
    at = ask(start(), "Pythonでゲームを作ってください。")
    assert "売上についての質問" in all_text(at) and "エラーが発生しました" not in all_text(at) and not at.exception
