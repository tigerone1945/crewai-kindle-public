"""Spike 3: Task の Structured Output (第3章)。"""
from crewai import Agent, Crew, Task
from pydantic import BaseModel

from common import get_llm
from s02_tools import CALLS, get_sales_by_region


class SalesAnalysisResult(BaseModel):
    question: str
    analysis_type: str
    result_summary: str
    key_findings: list[str]
    next_action: str


agent = Agent(
    role="売上分析アシスタント",
    goal="Toolの結果だけを根拠に売上を分析し、決められた形式で返す",
    backstory="数値は自分で計算せず、必ずToolの結果を使う。",
    tools=[get_sales_by_region],
    llm=get_llm(),
)
task = Task(
    description=(
        "質問: 地域別の売上を分析してください。\n"
        "analysis_typeは短い英語識別子、key_findingsは2〜4件、next_actionは1つ。"
    ),
    expected_output="SalesAnalysisResult形式の分析結果",
    agent=agent,
    output_pydantic=SalesAnalysisResult,
)
out = Crew(agents=[agent], tasks=[task]).kickoff()

print("CALLS:", CALLS)
print("raw type:", type(out.raw).__name__)
print("pydantic type:", type(out.pydantic).__name__)
print("dict:", out.pydantic.model_dump() if out.pydantic else None)
print("task output pydantic:", type(out.tasks_output[0].pydantic).__name__)
