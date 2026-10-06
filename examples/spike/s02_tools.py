"""Spike 2: Tool定義とAgent/Task単位の指定 (第2章)。"""
from crewai import Agent, Crew, Task
from crewai.tools import tool

from common import get_llm, load_sales

CALLS: list[str] = []


@tool("get_sales_summary")
def get_sales_summary() -> str:
    """売上データ全体の基本集計を返す。売上金額には sales_amount を使用する。"""
    CALLS.append("get_sales_summary")
    df = load_sales()
    return (
        f"総レコード数: {len(df)}\n売上合計: {int(df['sales_amount'].sum()):,}円\n"
        f"キャンセル件数: {int((df['status'] == 'キャンセル').sum())}\n"
        f"要レビュー件数: {int((df['review_flag'] == '要レビュー').sum())}"
    )


@tool("get_sales_by_region")
def get_sales_by_region() -> str:
    """地域別の売上を集計する。売上金額には sales_amount を使用する。"""
    CALLS.append("get_sales_by_region")
    df = load_sales()
    s = df.groupby("region")["sales_amount"].sum().sort_values(ascending=False)
    return "\n".join(f"{k}: {int(v):,}円" for k, v in s.items())


def run(question: str, tools_on: str) -> None:
    CALLS.clear()
    agent = Agent(
        role="売上分析アシスタント",
        goal="質問に応じて適切なToolを選び、Toolの結果だけを根拠に説明する",
        backstory="数値は自分で計算せず、必ずToolの結果を使う。",
        tools=[get_sales_summary, get_sales_by_region] if tools_on == "agent" else [],
        llm=get_llm(),
    )
    task = Task(
        description=question,
        expected_output="Toolの結果に基づく簡潔な日本語の説明",
        agent=agent,
        tools=[get_sales_summary, get_sales_by_region] if tools_on == "task" else None,
    )
    out = Crew(agents=[agent], tasks=[task]).kickoff()
    print(f"--- tools_on={tools_on} / {question}")
    print("CALLS:", CALLS)
    print("RAW:", out.raw[:300])
    print("Tool type:", type(get_sales_summary).__name__,
          "| name:", get_sales_summary.name)


if __name__ == "__main__":
    run("売上全体の状況を教えてください。", "agent")
    run("地域別の売上を比較してください。", "task")
