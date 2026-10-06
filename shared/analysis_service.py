"""分析サービス: 画面やAPIから呼ぶ入口。CrewAIの詳細（Agent/Task/Crew）はここに閉じ込める（第3章で導入）。

呼び出し側は run_analysis() と、InputRejected（入力が範囲外）・AnalysisError（出力を得られなかった）だけを知っていればよい。
"""
from crewai import Agent, Crew, Process, Task
from pydantic import ValidationError

from shared.conversation import Turn, build_history_text
from shared.guardrails import InputRejected, OutputGuard, check_question  # noqa: F401  InputRejected は呼び出し側のために再公開する
from shared.roles import build_agents, build_tasks
from shared.sales_data import get_llm
from shared.sales_db import SalesRepository
from shared.sales_tools import SALES_TOOLS
from shared.schemas import SalesAnalysisResult

MAX_ATTEMPTS = 3  # Crew を作り直す回数の上限（初回を含む）
PROCESSES = ("sequential", "hierarchical")
DEFAULT_PROCESS = "sequential"
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


def _run_guarded(make_crew, base_prompt: str, max_attempts: int, task_retries: int) -> SalesAnalysisResult:
    """Guardrail 付きで Crew を実行する。却下されたら、理由を質問に加えて Crew を作り直す。単一 Agent と複数 Role で共通。

    make_crew(prompt, guard) は、最終の Task に guard.check を Guardrail として渡した Crew を返す。
    """
    guard = OutputGuard(SalesRepository.default())
    prompt = base_prompt
    for attempt in range(1, max_attempts + 1):
        crew = make_crew(prompt, guard, task_retries)
        try:
            crew.kickoff()
        except ValidationError as e:  # Schema（契約）に適合しなかった
            raise AnalysisError(f"Schemaに適合しません: {e.errors()[0]['msg']}") from e
        except Exception as e:
            if not guard.is_exhausted(e):  # リトライを使い切ったときだけ、素の Exception が送出される。それ以外（通信など）は握りつぶさない
                raise
            if attempt == max_attempts:
                raise AnalysisError(f"{guard.checks}回検査しましたが通りませんでした: {guard.last_reason}") from e
            prompt = f"{base_prompt}\n\n前回の回答は次の理由で却下されました。直して再度回答してください: {guard.last_reason}"
            continue
        if guard.result is None:  # 検査に通った結果がない（Guardrailが働かなかった）。検査なしの結果は返さない
            raise AnalysisError("検査を通った結果を得られませんでした")
        return guard.result
    raise AssertionError("到達しない")  # pragma: no cover


def run_analysis(
    question: str,
    history: tuple[Turn, ...] | list[Turn] = (),
    preferences: list[str] | tuple[str, ...] = (),
    max_attempts: int = MAX_ATTEMPTS,
    task_retries: int = TASK_RETRIES,
) -> SalesAnalysisResult:
    """質問を分析し、型付きの結果を返す（単一の Agent）。

    - 入力が安全の境界の外なら、Crew を呼ばずに InputRejected を送出する（第7章）。
    - 出力は Task Guardrail が検査する。通らなければ、却下の理由を質問に加えて、Crew を作り直す（全体で max_attempts 回まで）。
      Task の中の再生成（task_retries）は、会話の続きとして理由を渡すため、同じ誤りを繰り返しやすい（Spike s17）。既定は使わない。
    - すべて使い切ったら AnalysisError を送出する（呼び出し側で扱う）。
    """
    question = check_question(question, has_history=bool(history))
    base = compose_prompt(question, history, preferences)
    return _run_guarded(
        lambda prompt, guard, retries: build_crew(prompt, guardrail=guard.check, guardrail_max_retries=retries),
        base, max_attempts, task_retries,
    )


def run_multi_role_analysis(
    question: str,
    max_attempts: int = MAX_ATTEMPTS,
    task_retries: int = TASK_RETRIES,
    process: str = DEFAULT_PROCESS,
) -> SalesAnalysisResult:
    """データ品質・売上分析・業務示唆の3つの Role で分析する（第11章）。実行の制御（process）は第12章。

    入力の検査と、出力の検査（最終の Task の Guardrail）は、単一の Agent と同じ。Role を分けても、安全の境界は変わらない。
    """
    question = check_question(question)
    return _run_guarded(
        lambda prompt, guard, retries: build_role_crew(prompt, guardrail=guard.check, guardrail_max_retries=retries, process=process),
        question, max_attempts, task_retries,
    )


def build_role_crew(question: str, guardrail=None, guardrail_max_retries: int = 0, process: str = DEFAULT_PROCESS) -> Crew:
    """3つの Role の Crew。process は "sequential"（既定。順序と担当をコードで決める）か "hierarchical"（Manager が決める）。

    sequential では、Agent に委譲（allow_delegation）を許さない。誰が何をするかは、Task の定義で決まっている。
    hierarchical では、Task に担当を決めず、Manager（manager_llm）が割り当てる。順序も担当も LLM が決めるため、結果が毎回変わる（Spike s20）。
    """
    if process not in PROCESSES:
        raise ValueError(f"未対応の process です: {process}（対応: {PROCESSES}）")
    agents = build_agents()
    hierarchical = process == "hierarchical"
    # Guardrail は最終の Task にだけ付ける（前工程は事実の報告で、検査の対象は最終の出力）
    tasks = build_tasks(agents, question, guardrail=guardrail, guardrail_max_retries=guardrail_max_retries, assign=not hierarchical)
    if hierarchical:
        return Crew(agents=list(agents.values()), tasks=tasks, process=Process.hierarchical, manager_llm=get_llm())
    return Crew(agents=list(agents.values()), tasks=tasks, process=Process.sequential)
