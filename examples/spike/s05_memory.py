"""Spike 5: Memory と Follow-up (第5章 / Evaluation Case 03)。"""
import tempfile

from crewai import Agent, Crew, Task
from crewai.memory.unified_memory import Memory

from common import MODEL, get_llm
from s02_tools import get_sales_by_region


def make_crew(question: str, memory):
    agent = Agent(
        role="売上分析アシスタント",
        goal="Toolの結果を根拠に売上を分析し、前の会話の文脈を踏まえて答える",
        backstory="数値は自分で計算せずToolを使う。",
        tools=[get_sales_by_region],
        llm=get_llm(),
    )
    task = Task(description=question, expected_output="簡潔な日本語の回答", agent=agent)
    return Crew(agents=[agent], tasks=[task], memory=memory)


def follow_up(label: str, memory) -> None:
    c1 = make_crew("地域別の売上を分析してください。", memory).kickoff()
    c2 = make_crew("では一番高かった地域は？", memory).kickoff()
    print(f"[{label}] 2ターン目: {c2.raw[:160]!r}")
    print(f"[{label}] '東北'を含む: {'東北' in c2.raw}")


# 1) Memory なし
follow_up("memoryなし", None)

# 2) Crew に Memory を渡す（保存先は一時ディレクトリ）
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
    mem = Memory(llm=MODEL, storage=d)
    follow_up("memoryあり", mem)
    mem.drain_writes()
    print("保存レコード数:", len(mem.list_records()))

# 3) 明示的な remember / recall とScope（Preference想定）
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
    mem = Memory(llm=MODEL, storage=d)
    mem.remember("このユーザーは結果を表形式で見たがる", scope="/user-001/preferences")
    mem.remember("このユーザーはキャンセル除外後の売上を好む", scope="/user-002/preferences")
    mem.drain_writes()
    hits = mem.recall("出力形式の好み", scope="/user-001/preferences", limit=3)
    print("user-001 recall:", [h.record.content for h in hits])
    hits2 = mem.recall("出力形式の好み", scope="/user-002/preferences", limit=3)
    print("user-002 recall:", [h.record.content for h in hits2])
