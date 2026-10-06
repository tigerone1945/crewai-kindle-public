"""第2章 ToolでCSV売上データを分析する。

Agentは質問に応じてToolを選び、Toolの結果だけを根拠に説明する。数値の計算はToolが行う。
使い方: uv run python chapters/ch02-tools/main.py "地域別の売上を比較してください。"
"""
import sys
from pathlib import Path

from crewai import Agent, Crew, Process, Task

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートとこの章を import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared.sales_data import get_llm  # noqa: E402
from sales_tools import SALES_TOOLS  # noqa: E402

DEFAULT_QUESTION = "カテゴリ別の売上を教えてください。"


def build_crew(question: str) -> Crew:
    analyst = Agent(
        role="売上分析アシスタント",
        goal="質問に応じて適切なToolを選び、Toolの結果だけを根拠に説明する",
        backstory=(
            "あなたは売上データの分析を支援する。"
            "売上の合計・構成比などの数値は自分で計算せず、必ずToolの結果をそのまま使う。"
            "Toolで答えられない質問には、推測で数値を作らず、できないと伝える。"
        ),
        tools=SALES_TOOLS,
        llm=get_llm(),
    )
    task = Task(
        description=question,
        expected_output="Toolの結果に基づく、簡潔で分かりやすい日本語の説明",
        agent=analyst,
    )
    return Crew(agents=[analyst], tasks=[task], process=Process.sequential)


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    print(build_crew(question).kickoff().raw)


if __name__ == "__main__":
    main()
