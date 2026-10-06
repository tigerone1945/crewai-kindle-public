"""Spike 1: Agent / Task / Crew の最小構成 (第1章)。"""
from crewai import Agent, Crew, Process, Task

from common import get_llm, load_sales

df = load_sales()
profile = (
    f"レコード数: {len(df)}\n列名: {', '.join(df.columns)}\n"
    f"注文日: {df['order_date'].min()} 〜 {df['order_date'].max()}"
)

analyst = Agent(
    role="売上分析アシスタント",
    goal="売上データの概要から、最初に確認すべき分析観点を提案する",
    backstory="あなたは売上・受注データの分析を支援する。数値集計は行わず観点の提案に徹する。",
    llm=get_llm(),
    verbose=False,
)
task = Task(
    description=f"次のDataset Profileを読み、最初に確認すべき分析観点を3つ提案してください。\n\n{profile}",
    expected_output="分析観点3つの箇条書き（各1行の理由つき）",
    agent=analyst,
)
crew = Crew(agents=[analyst], tasks=[task], process=Process.sequential, verbose=False)

result = crew.kickoff()
print("TYPE:", type(result).__name__)
print("RAW:", result.raw)
print("TOKENS:", result.token_usage)
print("TASKS_OUTPUT:", [type(t).__name__ for t in result.tasks_output])
