"""分析サービス: 画面やAPIから呼ぶ入口。CrewAIの詳細（Agent/Task/Crew）はここに閉じ込める（第3章で導入）。

呼び出し側は run_analysis() と、InputRejected（入力が範囲外）・AnalysisError（出力を得られなかった）だけを知っていればよい。
"""
from crewai import Agent, Crew, Process, Task
from pydantic import ValidationError

from shared.conversation import Turn, build_history_text
from shared.guardrails import InputRejected, OutputGuard, check_question  # noqa: F401  InputRejected は呼び出し側のために再公開する
from shared.sales_data import get_llm
from shared.sales_db import SalesRepository
from shared.sales_tools import SALES_TOOLS
from shared.schemas import SalesAnalysisResult

MAX_ATTEMPTS = 3  # Crew を作り直す回数の上限（初回を含む）
TASK_RETRIES = 0  # Task の中での再生成の回数（Spike s17 で決める）


class AnalysisError(RuntimeError):
    """分析結果をSchemaどおりに得られなかった。"""


def compose_prompt(question: str, history: tuple[Turn, ...] | list[Turn] = (), preferences: list[str] | tuple[str, ...] = ()) -> str:
    """質問に、好み（長期Memory）と会話の文脈（短期）を添えてAgentへ渡す文章を作る。

    好みと文脈は参照用であり、数値の根拠にはならない。数値は必ずToolの結果を使う。
    """
    blocks = []
    if preferences:
        blocks.append("ユーザーの好み（表現・形式の指示。数値の根拠にはしない）:\n" + "\n".join(f"- {p}" for p in preferences))
    if history:
        blocks.append("これまでの会話（文脈の参照用。数値は必ずToolで取得し直す）:\n" + build_history_text(history))
    blocks.append(("今回の質問:\n" if blocks else "") + question)
    return "\n\n".join(blocks)


def build_crew(question: str, guardrail=None, guardrail_max_retries: int = 0) -> Crew:
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
        name="sales_analysis",  # Traceに残る名前。未指定だと description（質問文を含む）が名前になってしまう
        description=(
            f"質問: {question}\n\n"
            "analysis_type は sales_summary / category_sales / region_sales / customer_type_sales / product_sales / unsupported のいずれか。\n"
            "metrics にはToolの結果から引用した数値だけを入れる。insights は2〜4件、recommendations は1〜3件。\n"
            "文章（summary / insights / recommendations）に数値を書くときは、Toolの結果に書かれている金額・構成比・件数を"
            "そのまま使う。合計・差・比率を新たに計算したり、万円などに丸めたりしない。\n"
            "特に、複数の地域・カテゴリ・顧客種別の構成比を足して「上位2地域で全体の○%」のように書かない。"
            "複数を比べるときは、それぞれの構成比を並べて書くか、数値を使わずに書く。"
        ),
        expected_output="SalesAnalysisResult の形式の分析結果",
        agent=analyst,
        output_pydantic=SalesAnalysisResult,
        guardrail=guardrail,
        guardrail_max_retries=guardrail_max_retries,
    )
    return Crew(agents=[analyst], tasks=[task], process=Process.sequential)


def run_analysis(
    question: str,
    history: tuple[Turn, ...] | list[Turn] = (),
    preferences: list[str] | tuple[str, ...] = (),
    max_attempts: int = MAX_ATTEMPTS,
    task_retries: int = TASK_RETRIES,
) -> SalesAnalysisResult:
    """質問を分析し、型付きの結果を返す。

    - 入力が安全の境界の外なら、Crew を呼ばずに InputRejected を送出する（第7章）。
    - 出力は Task Guardrail が検査する。通らなければ、Task の中で理由を伝えて再生成させる（task_retries 回まで）。
    - それでも通らなければ、却下の理由を質問に加えて、Crew を作り直して最初からやり直す（全体で max_attempts 回まで）。
      Task の中の再生成は、会話の続きとして理由を渡すため、同じ誤りを繰り返しやすい（Spike s17）。作り直しは、新しい文脈で理由を渡せる。
    - すべて使い切ったら AnalysisError を送出する（呼び出し側で扱う）。
    """
    question = check_question(question, has_history=bool(history))
    guard = OutputGuard(SalesRepository.default())
    base = compose_prompt(question, history, preferences)
    prompt = base
    for attempt in range(1, max_attempts + 1):
        crew = build_crew(prompt, guardrail=guard.check, guardrail_max_retries=task_retries)
        try:
            crew.kickoff()
        except ValidationError as e:  # Schema（契約）に適合しなかった
            raise AnalysisError(f"Schemaに適合しません: {e.errors()[0]['msg']}") from e
        except Exception as e:
            if not guard.is_exhausted(e):  # リトライを使い切ったときだけ、素の Exception が送出される。それ以外（通信など）は握りつぶさない
                raise
            if attempt == max_attempts:
                raise AnalysisError(f"{guard.checks}回検査しましたが通りませんでした: {guard.last_reason}") from e
            prompt = f"{base}\n\n前回の回答は次の理由で却下されました。直して再度回答してください: {guard.last_reason}"
            continue
        if guard.result is None:  # 検査に通った結果がない（Guardrailが働かなかった）。検査なしの結果は返さない
            raise AnalysisError("検査を通った結果を得られませんでした")
        return guard.result
    raise AssertionError("到達しない")  # pragma: no cover
