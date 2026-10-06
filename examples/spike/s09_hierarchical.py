"""Spike 9: Hierarchical Process と Delegation (第12章)。"""
import time

from crewai import Crew, Process, Task
from crewai.events import BaseEventListener
from crewai.events.types.tool_usage_events import ToolUsageStartedEvent

from common import get_llm
from roles import QUESTION, build_agents

LOG: list[str] = []


class L(BaseEventListener):
    def setup_listeners(self, bus):
        @bus.on(ToolUsageStartedEvent)
        def _t(source, event): LOG.append(f"{getattr(event, 'agent_role', '?')}:{event.tool_name}")


L()
dq, an, bi = build_agents()
task = Task(description=QUESTION, expected_output="品質確認・売上傾向・業務上の示唆（入力にない数値は使わない）")
crew = Crew(agents=[dq, an, bi], tasks=[task], process=Process.hierarchical, manager_llm=get_llm())
t0 = time.time()
try:
    out = crew.kickoff()
    print(f"[Hierarchical] {time.time() - t0:.1f}s tokens={out.token_usage.total_tokens} requests={out.token_usage.successful_requests}")
    print("tool log:", LOG)
    print("task agent:", [t.agent for t in out.tasks_output])
    print("delegations:", task.delegations if hasattr(task, "delegations") else None)
    print("final:", out.raw[:250].replace("\n", " "))
except Exception as e:
    print("FAILED", type(e).__name__, str(e)[:300])
