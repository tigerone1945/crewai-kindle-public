"""Spike 7: Event Bus による実行追跡 (第6章)。"""
import time

from crewai import Agent, Crew, Task
from crewai.events import BaseEventListener, crewai_event_bus
from crewai.events.types.task_events import TaskCompletedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import (ToolUsageFinishedEvent,
                                                   ToolUsageStartedEvent)

from common import get_llm
from s02_tools import get_sales_by_region

TRACE: list[tuple[str, str, float]] = []


class TraceListener(BaseEventListener):
    def setup_listeners(self, bus):
        @bus.on(TaskStartedEvent)
        def _ts(source, event): TRACE.append(("task_started", getattr(event, "task_name", "") or "", time.time()))

        @bus.on(ToolUsageStartedEvent)
        def _tus(source, event): TRACE.append(("tool_started", event.tool_name, time.time()))

        @bus.on(ToolUsageFinishedEvent)
        def _tuf(source, event): TRACE.append(("tool_finished", event.tool_name, time.time()))

        @bus.on(TaskCompletedEvent)
        def _tc(source, event): TRACE.append(("task_completed", getattr(event, "task_name", "") or "", time.time()))


listener = TraceListener()
agent = Agent(role="売上分析アシスタント", goal="Toolで集計して答える", backstory="数値はToolを使う。",
              tools=[get_sales_by_region], llm=get_llm())
task = Task(description="地域別の売上を教えてください。", expected_output="簡潔な回答", agent=agent)
out = Crew(agents=[agent], tasks=[task]).kickoff()

t0 = TRACE[0][2] if TRACE else 0
for kind, name, ts in TRACE:
    print(f"+{ts - t0:5.2f}s {kind:15s} {name}")
print("token_usage:", out.token_usage.total_tokens)
