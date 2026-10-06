"""Spike 5c: Preference を別Sessionで思い出せるか (CrewAI固有 Case 候補)。"""
import re
import tempfile

from crewai import Agent, Crew, Task
from crewai.memory.unified_memory import Memory

from common import MODEL, get_llm
from s02_tools import get_sales_by_region


def kick(question, memory):
    agent = Agent(role="売上分析アシスタント", goal="Toolの結果を根拠に、ユーザーの好みを踏まえて答える",
                  backstory="数値は自分で計算せずToolを使う。", tools=[get_sales_by_region], llm=get_llm())
    task = Task(description=question, expected_output="簡潔な日本語の回答", agent=agent)
    return Crew(agents=[agent], tasks=[task], memory=memory).kickoff().raw


def has_table(text):
    return bool(re.search(r"^\s*\|.+\|\s*$", text, re.M))


def run(label, memory):
    kick("覚えておいてください。私は分析結果を必ず表形式で見たいです。", memory)  # Session A
    r = kick("地域別の売上を比較してください。", memory)  # Session B（別Crew）
    print(f"[{label}] table={has_table(r)} | {r[:90]!r}")
    return r


if __name__ == "__main__":
    run("memoryなし", None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        mem = Memory(llm=MODEL, storage=d)
        run("Crew memory(自動)", mem)
        mem.drain_writes()
        contents = [r.content for r in mem.list_records()]
        print("保存内容に売上数値が含まれる:", any(re.search(r"\d{1,3}(,\d{3}){1,}", c) for c in contents))
        print("保存件数:", len(contents))
