"""第1章 CrewAIの最小構成: Agent / Task / Crew。

CSV全体ではなくDataset Profileだけを渡し、最初に確認すべき分析観点を3つ提案させる。
この章のAgentは数値を集計しない（集計は第2章でToolへ委譲する）。
"""
import sys
from pathlib import Path

from crewai import Agent, Crew, Process, Task

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.dataset_profile import build_dataset_profile  # noqa: E402
from shared.sales_data import get_llm  # noqa: E402
from shared.sales_db import SalesRepository  # noqa: E402


def build_crew(dataset_profile: str) -> Crew:
    analyst = Agent(
        role="売上分析アシスタント",
        goal="売上データの概要から、最初に確認すべき分析観点を提案する",
        backstory=(
            "あなたは売上・受注データの分析を支援する。"
            "数値の集計は行わず、どの観点を確認すべきかの提案に徹する。"
        ),
        llm=get_llm(),
    )
    task = Task(
        description=(
            "次のDataset Profileを読み、最初に確認すべき分析観点を3つ提案してください。\n\n"
            f"{dataset_profile}"
        ),
        expected_output="分析観点3つの箇条書き（各1行の理由つき）",
        agent=analyst,
    )
    return Crew(agents=[analyst], tasks=[task], process=Process.sequential)


def main() -> None:
    profile = build_dataset_profile(SalesRepository.default().to_dataframe())
    result = build_crew(profile).kickoff()
    print(result.raw)


if __name__ == "__main__":
    main()
