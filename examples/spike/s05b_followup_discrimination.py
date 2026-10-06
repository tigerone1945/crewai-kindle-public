"""Spike 5b: Memory の有無でFollow-upの結果が変わる質問を探す (Evaluation Case 03 の再設計)。"""
import tempfile

from crewai import Agent, Crew, Task
from crewai.memory.unified_memory import Memory

from common import MODEL, get_llm
from s02_tools import CALLS, get_sales_by_region, get_sales_summary


def kick(question, memory):
    agent = Agent(role="売上分析アシスタント", goal="Toolの結果を根拠に、前の会話の文脈を踏まえて答える",
                  backstory="数値は自分で計算せずToolを使う。文脈が分からないときは推測せず聞き返す。",
                  tools=[get_sales_summary, get_sales_by_region], llm=get_llm())
    task = Task(description=question, expected_output="簡潔な日本語の回答", agent=agent)
    return Crew(agents=[agent], tasks=[task], memory=memory).kickoff().raw


def scenario(label, memory):
    CALLS.clear()
    kick("地域別の売上を分析してください。", memory)
    CALLS.clear()
    r = kick("では、その1位の地域の売上は全体の何%ですか？", memory)
    print(f"[{label}] tools={CALLS} | {r[:170]!r}")


scenario("memoryなし", None)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
    scenario("memoryあり", Memory(llm=MODEL, storage=d))
