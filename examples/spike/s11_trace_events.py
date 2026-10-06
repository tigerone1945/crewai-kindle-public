"""Spike 11: 第6章用。Event の順序・ContextVar の伝播・Event が運ぶ内容（機微情報）を確認する。"""
import contextvars
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai.events import BaseEventListener
from crewai.events.types.agent_events import AgentExecutionCompletedEvent, AgentExecutionStartedEvent
from crewai.events.types.crew_events import CrewKickoffCompletedEvent, CrewKickoffFailedEvent, CrewKickoffStartedEvent
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent, LLMCallStartedEvent
from crewai.events.types.task_events import TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent, ToolUsageStartedEvent

import shared.analysis_service as service

RUN = contextvars.ContextVar("run", default=None)
ROWS = []
TYPES = (CrewKickoffStartedEvent, CrewKickoffCompletedEvent, CrewKickoffFailedEvent, TaskStartedEvent, TaskCompletedEvent,
         TaskFailedEvent, AgentExecutionStartedEvent, AgentExecutionCompletedEvent, LLMCallStartedEvent,
         LLMCallCompletedEvent, LLMCallFailedEvent, ToolUsageStartedEvent, ToolUsageFinishedEvent, ToolUsageErrorEvent)


class L(BaseEventListener):
    def setup_listeners(self, bus):
        for t in TYPES:
            def make(t):
                @bus.on(t)
                def _h(source, event):
                    ROWS.append((event.emission_sequence, type(event).__name__, RUN.get(), event.timestamp, event))
            make(t)


L()
RUN.set("run-A")
r = service.run_analysis("地域別の売上を比較してください。", max_attempts=1)
for seq, name, run, ts, ev in sorted(ROWS, key=lambda x: x[0]):
    extra = ""
    if name.startswith("ToolUsage"):
        extra = f"tool={ev.tool_name} args={ev.tool_args!r}"
        if hasattr(ev, "output"): extra += f" out_len={len(str(ev.output))} started={getattr(ev,'started_at',None)} finished={getattr(ev,'finished_at',None)}"
    if name.startswith("LLMCall"):
        extra = f"model={ev.model} call_id={ev.call_id}" + (f" usage={ev.usage} finish={ev.finish_reason}" if name.endswith("CompletedEvent") else "")
    if name.startswith("Task") or name.startswith("Agent"):
        extra = f"task={getattr(ev,'task_name',None)!r} agent={getattr(ev,'agent_role',None)!r}"
    print(seq, name, run, extra)
