"""Execution Traceのテスト。本物のEventをEvent Busへ流すため、LLMは呼ばない。"""
import datetime
import json
import threading

import pytest
from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent
from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent

from shared.tracing import RunTrace, current_trace, record_app_event, trace_run


def tool_finished(name="get_sales_by_region", args=None, output="ok", ms=40):
    now = datetime.datetime.now()
    return ToolUsageFinishedEvent(tool_name=name, tool_args=args or {}, tool_class="T", started_at=now,
                                  finished_at=now + datetime.timedelta(milliseconds=ms), output=output, from_cache=False)


def llm_done(prompt=10, completion=5):
    return LLMCallCompletedEvent(model="gpt-4o-mini", call_id="c1", messages="x", response="y", call_type="llm_call",
                                 usage={"prompt_tokens": prompt, "completion_tokens": completion}, finish_reason="stop")


def emit(*events):
    for e in events:
        crewai_event_bus.emit(None, e)


def test_tool_and_llm_calls_are_recorded_with_summary():
    with trace_run() as trace:
        emit(tool_finished(ms=40), llm_done(10, 5), llm_done(20, 7))
    s = trace.summary()
    assert s["tool_calls"] == ["get_sales_by_region"]
    assert s["llm_calls"] == 2 and s["prompt_tokens"] == 30 and s["completion_tokens"] == 12
    tool = next(e for e in trace.events if e.kind == "tool")
    assert tool.duration_ms == 40 and tool.status == "ok"


def test_failures_are_recorded_as_errors():
    with trace_run() as trace:
        emit(ToolUsageErrorEvent(tool_name="t", tool_args={}, tool_class="T", error="boom"),
             LLMCallFailedEvent(model="m", call_id="c", error="rate limited"))
    assert trace.summary()["errors"] == 2
    assert {e.name for e in trace.events if e.status == "error"} == {"t", "m"}


def test_sensitive_content_never_reaches_the_trace():
    """Eventは質問・引数の値・Toolの出力・LLMの応答を運ぶが、Traceには入らない（BR-10）。"""
    with trace_run() as trace:
        emit(tool_finished(args={"customer": "山田太郎 taro@example.com"}, output="SECRET-OUTPUT-BODY"),
             ToolUsageErrorEvent(tool_name="t", tool_args={"k": "v"}, tool_class="T",
                                 error="login failed password: hunter2 for taro@example.com sk-abcdef123456"),
             LLMCallCompletedEvent(model="m", call_id="c", messages="PROMPT-WITH-QUESTION", response="LLM-RESPONSE",
                                   call_type="llm_call", usage={}))
    dumped = trace.to_json()
    for leaked in ("山田太郎", "taro@example.com", "SECRET-OUTPUT-BODY", "hunter2", "sk-abcdef123456", "PROMPT-WITH-QUESTION", "LLM-RESPONSE"):
        assert leaked not in dumped
    tool = next(e for e in trace.events if e.kind == "tool" and e.status == "ok")
    assert tool.attrs["arg_names"] == "customer"  # 値は残さず、どの引数を使ったかだけ残す
    assert tool.attrs["output_chars"] == len("SECRET-OUTPUT-BODY")


def test_without_an_active_trace_events_are_ignored_and_app_events_are_noop():
    emit(tool_finished())
    record_app_event("attempt", "analysis")
    assert current_trace() is None


def test_trace_is_closed_after_the_block():
    with trace_run() as trace:
        pass
    emit(tool_finished())
    assert trace.events == []
    assert current_trace() is None


def test_concurrent_runs_do_not_mix():
    traces, errors = {}, []

    def run(name, tool):
        try:
            with trace_run(name) as t:
                emit(tool_finished(name=tool))
            traces[name] = t
        except Exception as e:  # スレッド内の失敗をテスト本体へ伝える
            errors.append(e)

    threads = [threading.Thread(target=run, args=(f"run-{i}", f"tool_{i}")) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    for i in range(4):
        assert traces[f"run-{i}"].summary()["tool_calls"] == [f"tool_{i}"]


def test_app_events_are_added_in_order():
    with trace_run() as trace:
        record_app_event("attempt", "analysis", "error", attempt=1, error="x")
        record_app_event("attempt", "analysis", attempt=2)
    s = trace.summary()
    assert s["attempts"] == 2 and s["errors"] == 1
    assert s["status"] == "ok"  # 再試行で成功した実行。途中の失敗はerrorsに残る


def test_run_is_an_error_when_the_last_attempt_fails():
    with trace_run() as trace:
        record_app_event("attempt", "analysis", "ok", attempt=1)
        record_app_event("attempt", "analysis", "error", attempt=2)
    assert trace.summary()["status"] == "error"


def test_duration_covers_the_whole_run_including_retries():
    trace = RunTrace("r")
    trace.add("crew", "crew", at=trace.started_at + 2.0, duration_ms=2000)
    trace.add("crew", "crew", at=trace.started_at + 5.0, duration_ms=2500)
    assert trace.summary()["duration_ms"] == 5000


def test_duration_is_taken_from_the_start_event():
    trace = RunTrace("r")
    trace.mark_started("e1", trace.started_at + 1.0)
    trace.add("task", "t", at=trace.started_at + 3.5, started_event_id="e1")
    assert trace.events[0].duration_ms == 2500 and trace.events[0].at_ms == 3500


def test_save_writes_valid_json(tmp_path):
    with trace_run("abc123") as trace:
        emit(tool_finished())
    path = trace.save(tmp_path)
    assert path.name == "abc123.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["summary"]["run_id"] == "abc123" and data["events"][0]["kind"] == "tool"
