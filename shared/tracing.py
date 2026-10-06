"""Execution Trace（第6章）: 1回の実行で、Crew の中で何が起きたかを構造化して残す。

Trace と Application Log は目的が違う（BR-09）。
- Trace: 1回の実行の記録。「どの Task で、どの Tool を、何回、どれだけの時間で呼んだか」を、あとから1件ずつ辿る。
- Application Log: アプリケーションの運用の記録。「分析が失敗した」などの出来事を、時系列で流す。

CrewAI の Event Bus を購読して記録する。Crew の定義は変えない（観測と処理を分ける）。

Traceに残すもの（BR-10）:
  残す: 種別・名前・状態・所要時間・回数・Token数・Toolの引数の名前・出力の長さ・エラーの種類
  残さない: 質問文・プロンプト・LLMの応答・Toolの引数の値・Toolの出力の中身
  エラーメッセージだけは原因の調査に必要なため残すが、マスクして切り詰める（shared/sensitive.py）
Event が運ぶ内容（messages、tool_args、output など）は便利だが、利用者の入力や顧客情報を含みうる。
そこで、記録してよい項目を「許可リスト」として TraceEvent に書き出し、それ以外は構造上、残せないようにしている。
"""
import json
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from crewai.events import BaseEventListener, crewai_event_bus
from crewai.events.types.agent_events import (AgentExecutionCompletedEvent, AgentExecutionErrorEvent,
                                              AgentExecutionStartedEvent)
from crewai.events.types.crew_events import (CrewKickoffCompletedEvent, CrewKickoffFailedEvent,
                                             CrewKickoffStartedEvent)
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent, LLMCallStartedEvent
from crewai.events.types.task_events import TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent

from shared.sales_data import ROOT
from shared.sensitive import redact

TRACE_DIR = ROOT / "data" / "traces"

Attr = str | int | float | bool | None


@dataclass(frozen=True)
class TraceEvent:
    """Trace の1行。attrs には、記録してよいと決めた値だけを入れる。"""

    kind: str  # crew / task / agent / llm / tool / attempt
    name: str
    status: str  # ok / error
    at_ms: int  # 実行開始からの経過時間
    duration_ms: int | None = None
    attrs: Mapping[str, Attr] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "status": self.status, "at_ms": self.at_ms,
                "duration_ms": self.duration_ms, **self.attrs}


class RunTrace:
    """1回の実行の Trace。別スレッドで動く Event Handler から追記されるため、Lock で守る。"""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.started_at = time.time()
        self._events: list[TraceEvent] = []
        self._started: dict[str, float] = {}  # event_id → 開始時刻（終了Eventと突き合わせて所要時間を出す）
        self._lock = threading.Lock()

    def mark_started(self, event_id: str, at: float) -> None:
        with self._lock:
            self._started[event_id] = at

    def add(self, kind: str, name: str, status: str = "ok", *, at: float | None = None,
            started_event_id: str | None = None, duration_ms: int | None = None, **attrs: Attr) -> None:
        at = time.time() if at is None else at
        with self._lock:
            start = self._started.pop(started_event_id, None) if started_event_id else None
            if duration_ms is None and start is not None:
                duration_ms = round((at - start) * 1000)
            self._events.append(TraceEvent(kind, name, status, round((at - self.started_at) * 1000), duration_ms, attrs))

    @property
    def events(self) -> list[TraceEvent]:
        with self._lock:
            return sorted(self._events, key=lambda e: e.at_ms)

    def summary(self) -> dict:
        """画面や運用で最初に見る要約。細部は events を辿る。"""
        events = self.events
        llm = [e for e in events if e.kind == "llm"]
        tools = [e for e in events if e.kind == "tool"]
        attempts = [e for e in events if e.kind == "attempt"]
        # 再試行して最終的に成功した実行は ok。途中の失敗は errors と attempts に残る
        run_events = attempts or [e for e in events if e.kind in ("crew", "task")]
        return {
            "run_id": self.run_id,
            "status": run_events[-1].status if run_events else "ok",
            "duration_ms": events[-1].at_ms if events else None,  # 再試行を含む、実行全体の所要時間
            "attempts": len(attempts),
            "llm_calls": len(llm),
            "tool_calls": [e.name for e in tools],
            "errors": sum(e.status == "error" for e in events),
            "prompt_tokens": sum(int(e.attrs.get("prompt_tokens") or 0) for e in llm),
            "completion_tokens": sum(int(e.attrs.get("completion_tokens") or 0) for e in llm),
        }

    def to_json(self) -> str:
        return json.dumps({"summary": self.summary(), "events": [e.to_dict() for e in self.events]},
                          ensure_ascii=False, indent=2)

    def save(self, directory: Path = TRACE_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path


_ACTIVE: ContextVar[RunTrace | None] = ContextVar("active_run_trace", default=None)


def current_trace() -> RunTrace | None:
    return _ACTIVE.get()


def record_app_event(kind: str, name: str, status: str = "ok", **attrs: Attr) -> None:
    """アプリケーション側の出来事（例: 再試行）を、実行中の Trace へ加える。Trace がなければ何もしない。"""
    trace = _ACTIVE.get()
    if trace is not None:
        trace.add(kind, name, status, **attrs)


@contextmanager
def trace_run(run_id: str | None = None) -> Iterator[RunTrace]:
    """この中で動いた Crew の実行を Trace に記録する。ContextVar で束縛するため、同時に動く別の実行とは混ざらない。"""
    trace = RunTrace(run_id)
    token = _ACTIVE.set(trace)
    try:
        yield trace
    finally:
        crewai_event_bus.flush()  # 非同期で処理中の Event を、Trace を閉じる前に取り込む
        _ACTIVE.reset(token)


def _error_text(event) -> str:
    return redact(str(getattr(event, "error", "") or ""))


def _agent_role(event) -> str:
    agent = getattr(event, "agent", None)
    return getattr(agent, "role", None) or getattr(event, "agent_role", None) or "unknown"


def _ts(event) -> float:
    return event.timestamp.timestamp()


class TraceCollector(BaseEventListener):
    """Event Bus の Event を、許可した項目だけに絞って、実行中の Trace へ書く。

    Event Bus への登録は1回だけにするため、モジュールで1つだけ作る（shared/tool_calls.py と同じ事情）。
    """

    def setup_listeners(self, bus) -> None:
        def start(event) -> RunTrace | None:
            trace = current_trace()
            if trace is not None:
                trace.mark_started(event.event_id, _ts(event))
            return trace

        @bus.on(CrewKickoffStartedEvent)
        def _crew_start(source, event):
            start(event)

        @bus.on(CrewKickoffCompletedEvent)
        def _crew_done(source, event):
            if trace := current_trace():
                trace.add("crew", "crew", at=_ts(event), started_event_id=event.started_event_id,
                          total_tokens=getattr(event, "total_tokens", None))

        @bus.on(CrewKickoffFailedEvent)
        def _crew_failed(source, event):
            if trace := current_trace():
                trace.add("crew", "crew", "error", at=_ts(event), started_event_id=event.started_event_id,
                          error=_error_text(event))

        @bus.on(TaskStartedEvent)
        def _task_start(source, event):
            start(event)

        @bus.on(TaskCompletedEvent)
        def _task_done(source, event):
            if trace := current_trace():
                trace.add("task", event.task.name or "task", at=_ts(event), started_event_id=event.started_event_id)

        @bus.on(TaskFailedEvent)
        def _task_failed(source, event):
            if trace := current_trace():
                trace.add("task", event.task.name or "task", "error", at=_ts(event),
                          started_event_id=event.started_event_id, error=_error_text(event))

        @bus.on(AgentExecutionStartedEvent)
        def _agent_start(source, event):
            start(event)

        @bus.on(AgentExecutionCompletedEvent)
        def _agent_done(source, event):
            if trace := current_trace():
                trace.add("agent", _agent_role(event), at=_ts(event), started_event_id=event.started_event_id)

        @bus.on(AgentExecutionErrorEvent)
        def _agent_failed(source, event):
            if trace := current_trace():
                trace.add("agent", _agent_role(event), "error", at=_ts(event),
                          started_event_id=event.started_event_id, error=_error_text(event))

        @bus.on(LLMCallStartedEvent)
        def _llm_start(source, event):
            start(event)

        @bus.on(LLMCallCompletedEvent)
        def _llm_done(source, event):
            if trace := current_trace():
                usage = event.usage or {}
                trace.add("llm", event.model or "unknown", at=_ts(event), started_event_id=event.started_event_id,
                          prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                          finish_reason=event.finish_reason)

        @bus.on(LLMCallFailedEvent)
        def _llm_failed(source, event):
            if trace := current_trace():
                trace.add("llm", event.model or "unknown", "error", at=_ts(event),
                          started_event_id=event.started_event_id, error=_error_text(event))

        @bus.on(ToolUsageFinishedEvent)
        def _tool_done(source, event):
            if trace := current_trace():
                args = event.tool_args if isinstance(event.tool_args, dict) else {}
                trace.add("tool", event.tool_name, at=event.finished_at.timestamp(),
                          duration_ms=round((event.finished_at - event.started_at).total_seconds() * 1000),
                          arg_names=",".join(sorted(args)), output_chars=len(str(event.output)),
                          from_cache=bool(event.from_cache))

        @bus.on(ToolUsageErrorEvent)
        def _tool_failed(source, event):
            if trace := current_trace():
                args = event.tool_args if isinstance(event.tool_args, dict) else {}
                trace.add("tool", event.tool_name, "error", at=_ts(event), arg_names=",".join(sorted(args)),
                          error=_error_text(event))


collector = TraceCollector()
