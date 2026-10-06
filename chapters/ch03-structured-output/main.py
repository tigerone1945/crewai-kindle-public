"""第3章 Structured Outputで分析結果を安定させる。

Taskの出力をPydanticのSchemaで受け取り、Applicationが扱える契約にする。
使い方: uv run python chapters/ch03-structured-output/main.py "地域別の売上を分析してください。"
"""
import sys
from pathlib import Path

from crewai import Agent, Crew, Process, Task
from pydantic import ValidationError

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sales_data import get_llm, load_sales  # noqa: E402
from shared.sales_tools import SALES_TOOLS  # noqa: E402
from shared.sales_validation import find_ungrounded_metrics, find_ungrounded_numbers_in_text  # noqa: E402
from shared.schemas import SalesAnalysisResult  # noqa: E402

DEFAULT_QUESTION = "地域別の売上を分析してください。"
MAX_ATTEMPTS = 3


class AnalysisError(RuntimeError):
    """分析結果をSchemaどおりに得られなかった。"""


def build_crew(question: str) -> Crew:
    analyst = Agent(
        role="売上分析アシスタント",
        goal="Toolの結果だけを根拠に売上を分析し、決められた形式で返す",
        backstory=(
            "あなたは売上データの分析を支援する。"
            "売上の合計・構成比などの数値は自分で計算せず、必ずToolの結果をそのまま使う。"
            "対応するToolがない質問（予測など）は、推測せず analysis_type を unsupported にする。"
        ),
        tools=SALES_TOOLS,
        llm=get_llm(),
    )
    task = Task(
        description=(
            f"質問: {question}\n\n"
            "analysis_type は sales_summary / category_sales / region_sales / unsupported のいずれか。\n"
            "metrics にはToolの結果から引用した数値だけを入れる。insights は2〜4件、recommendations は1〜3件。\n"
            "文章（summary / insights / recommendations）に数値を書くときは、Toolの結果に書かれている金額・構成比・件数を"
            "そのまま使う。合計・差・比率を新たに計算したり、万円などに丸めたりしない。"
        ),
        expected_output="SalesAnalysisResult の形式の分析結果",
        agent=analyst,
        output_pydantic=SalesAnalysisResult,
    )
    return Crew(agents=[analyst], tasks=[task], process=Process.sequential)


def _analyze_once(question: str) -> SalesAnalysisResult:
    try:
        output = build_crew(question).kickoff()
    except ValidationError as e:  # Schema（契約）に適合しなかった
        raise AnalysisError(f"Schemaに適合しません: {e.errors()[0]['msg']}") from e
    result = output.pydantic
    if not isinstance(result, SalesAnalysisResult):
        raise AnalysisError(f"Schemaに適合する結果を得られませんでした: {output.raw[:200]!r}")
    df = load_sales()
    problems = find_ungrounded_metrics(result, df) + find_ungrounded_numbers_in_text(result, df)
    if problems:
        raise AnalysisError("実データに裏づけのない数値があります: " + "; ".join(problems))
    return result


def run_analysis(question: str, max_attempts: int = MAX_ATTEMPTS) -> SalesAnalysisResult:
    """質問を分析し、型付きの結果を返す。

    Schemaを満たさない、または数値が実データと合わない場合は、理由を伝えて再試行する。
    再試行を使い切ったら AnalysisError を送出する（呼び出し側で扱う）。
    指示だけでは数値の混入を防ぎきれないため、検証と再試行で守る。第7章でGuardrailとして整理し直す。
    """
    prompt = question
    for attempt in range(1, max_attempts + 1):
        try:
            return _analyze_once(prompt)
        except AnalysisError as e:
            if attempt == max_attempts:
                raise AnalysisError(f"{max_attempts}回試しましたが検証を通りませんでした: {e}") from e
            prompt = f"{question}\n\n前回の回答は次の理由で却下されました。直して再度回答してください: {e}"
    raise AssertionError("到達しない")  # pragma: no cover


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    print(run_analysis(question).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
