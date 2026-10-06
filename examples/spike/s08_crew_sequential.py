"""Spike 8: 3 Role の Crew (Sequential) と 単一Agent の比較 (第11・12章 / Evaluation Case 07)。"""
import re
import time

from crewai import Agent, Crew, Process, Task
from crewai.events import BaseEventListener
from crewai.events.types.task_events import TaskStartedEvent
from crewai.events.types.tool_usage_events import ToolUsageStartedEvent

from common import get_llm
from roles import QUESTION, build_agents, build_tasks, check_data_quality
from s02_tools import get_sales_by_region, get_sales_summary

LOG: list[str] = []


class L(BaseEventListener):
    def setup_listeners(self, bus):
        @bus.on(ToolUsageStartedEvent)
        def _t(source, event): LOG.append(f"tool:{event.tool_name} by {getattr(event, 'agent_role', '?')}")


L()
dq, an, bi = build_agents()
t1, t2, t3 = build_tasks(dq, an, bi)
t0 = time.time()
out = Crew(agents=[dq, an, bi], tasks=[t1, t2, t3], process=Process.sequential).kickoff()
dt = time.time() - t0
print(f"[Crew3Role] {dt:.1f}s tokens={out.token_usage.total_tokens} requests={out.token_usage.successful_requests}")
print("tool log:", LOG)
print("task outputs:", [(t.agent, len(t.raw)) for t in out.tasks_output])
nums_in = {n for t in out.tasks_output[:2] for n in re.findall(r"\d[\d,]{3,}", t.raw)}
nums_bi = set(re.findall(r"\d[\d,]{3,}", out.tasks_output[2].raw))
print("insight introduces new numbers:", sorted(nums_bi - nums_in))
print("final:", out.raw[:300].replace("\n", " "))

solo = Agent(role="売上分析アシスタント", goal="データ品質確認・売上分析・業務示唆を行う", backstory="数値はToolを使う。",
             tools=[check_data_quality, get_sales_summary, get_sales_by_region], llm=get_llm())
LOG.clear()
t0 = time.time()
o2 = Crew(agents=[solo], tasks=[Task(description=QUESTION, expected_output="品質確認・売上傾向・示唆", agent=solo)]).kickoff()
print(f"[Single] {time.time() - t0:.1f}s tokens={o2.token_usage.total_tokens} requests={o2.token_usage.successful_requests}")
print("tool log:", LOG)
