"""Role（第11章）: 1人の Agent に集まっていた責務を、3つの Role に分ける。

| Role | 責務 | 持つ Tool | 持たないもの |
|---|---|---|---|
| data_quality（データ品質担当） | 分析の前に、データの品質を事実で報告する | check_data_quality | 売上の分析・示唆 |
| sales_analyst（売上分析担当） | Tool の結果だけを根拠に、売上の傾向を数値で報告する | 売上の集計 Tool（SALES_TOOLS） | 業務上の解釈・提言 |
| business_insight（業務示唆担当） | 前工程の結果から、業務上の示唆を整理する | なし | Tool。文章に数値を書くこと（数値は metrics にだけ） |

Role の分け方は、Tool（権限）の境界に合わせる。Tool を持たない Role は、データを直接読めない（Context Boundary）。
Task は Role ごとに1つ。前工程の出力を `context` で渡す。Task の名前は Trace に残る（質問文を含めない）。
"""
from crewai import Agent, Task
from crewai.tools import tool

from shared.sales_data import get_llm
from shared.sales_db import SalesRepository
from shared.sales_tools import SALES_TOOLS
from shared.schemas import SalesAnalysisResult

ROLE_NAMES = {
    "data_quality": "データ品質担当",
    "sales_analyst": "売上分析担当",
    "business_insight": "業務示唆担当",
}


@tool("check_data_quality")
def check_data_quality() -> str:
    """売上データの品質（件数・期間・取り込み時刻・キャンセルと要レビューの件数と割合・整合性の確認結果）を返す。"""
    q = SalesRepository.default().data_quality()
    return (
        f"総レコード数: {q['order_count']}\n期間: {q['period_start']} 〜 {q['period_end']}\n取り込み時刻: {q['imported_at']}\n"
        f"キャンセル件数: {q['cancelled_count']}（{q['cancelled_share_pct']}%）\n"
        f"要レビュー件数: {q['review_count']}（{q['review_share_pct']}%）\n"
        f"整合性の確認（いずれも0件が正常）: sales_amountがtotal_amountを超える注文 {q['sales_exceeds_total_count']}件 / "
        f"キャンセルなのにsales_amountが0でない注文 {q['cancelled_with_sales_count']}件 / "
        f"顧客または商品が存在しない注文 {q['orders_without_customer_or_product']}件"
    )


QUALITY_TOOLS = [check_data_quality]
# どの Role が、どの Tool を持つか。ここが権限の境界（テストで、Role 以外が実行していないことを確かめる）
ROLE_TOOLS = {
    "data_quality": [t.name for t in QUALITY_TOOLS],
    "sales_analyst": [t.name for t in SALES_TOOLS],
    "business_insight": [],
}


def build_agents() -> dict[str, Agent]:
    llm = get_llm()
    return {
        "data_quality": Agent(
            role=ROLE_NAMES["data_quality"],
            goal="分析の前に、データの品質を、Tool の結果の事実だけで報告する",
            backstory="データの品質確認だけを担当する。売上の分析や業務上の示唆は述べない。数値はToolの結果をそのまま使う。",
            tools=QUALITY_TOOLS, llm=llm, allow_delegation=False,
        ),
        "sales_analyst": Agent(
            role=ROLE_NAMES["sales_analyst"],
            goal="Toolの結果だけを根拠に、売上の傾向を数値で報告する",
            backstory="売上の合計・構成比などの数値は自分で計算せず、必ずToolの結果をそのまま使う。業務上の解釈や提言は述べない。",
            tools=SALES_TOOLS, llm=llm, allow_delegation=False,
        ),
        "business_insight": Agent(
            role=ROLE_NAMES["business_insight"],
            goal="前工程の結果から、業務上の示唆を整理する",
            backstory=(
                "Toolは持たない。前工程（データ品質・売上分析）の結果だけを使う。"
                "示唆は、数値を使わない言葉で書く。数値は、前工程の値をそのまま metrics に引用するだけ。"
                "入力にない数値や事実を作らない。合計・差・比率を新たに計算しない。"
            ),
            llm=llm, allow_delegation=False,
        ),
    }


def build_tasks(agents: dict[str, Agent], question: str, guardrail=None, guardrail_max_retries: int = 0) -> list[Task]:
    """Role ごとに1つの Task。前工程の出力は context で渡す（Context Boundary）。最後の Task が、アプリケーションとの契約（Schema）と Guardrail を持つ。

    Guardrail は、Task を作るときに渡す必要がある。作ったあとに代入しても、検査は呼ばれない（Spike s13）。
    """
    quality = Task(
        name="data_quality",
        description="売上データの品質を確認してください。問題の有無と、件数・割合を、Toolの結果のとおりに報告します。",
        expected_output="データ品質の確認結果（事実のみ）", agent=agents["data_quality"],
    )
    analysis = Task(
        name="sales_analysis",
        description=(
            "売上の全体状況と、地域別・カテゴリ別・顧客種別・商品別の傾向を、Toolで取得して数値で報告してください。"
            "合計・差・比率は自分で計算せず、Toolの結果の値をそのまま使います。"
        ),
        expected_output="売上の全体状況と傾向（数値はToolの結果のみ）", agent=agents["sales_analyst"], context=[quality],
    )
    insight = Task(
        name="business_insight",
        description=(
            f"質問: {question}\n\n"
            "前工程のデータ品質の確認結果と売上の分析結果だけから、業務上の示唆（発見・注意点・次の確認事項）をまとめてください。\n"
            "analysis_type は integrated_analysis にします。insights は2〜4件、recommendations は1〜3件です。\n"
            "数値は metrics にだけ入れます。前工程の結果に書かれている金額・割合・件数を、そのまま引用します。\n"
            "summary / insights / recommendations には、数値（金額・割合・件数）を書きません。"
            "「大きい」「偏っている」「差が小さい」のように、数値を使わない言葉で書きます。"
            "示唆を書くとき、合計・差・比率を新たに計算したくなりますが、それは行いません（BR-03）。"
        ),
        expected_output="SalesAnalysisResult の形式の統合分析", agent=agents["business_insight"],
        context=[quality, analysis], output_pydantic=SalesAnalysisResult,
        guardrail=guardrail, guardrail_max_retries=guardrail_max_retries,
    )
    return [quality, analysis, insight]


_KEY_BY_NAME = {name: key for key, name in ROLE_NAMES.items()}


def role_keys(display_names) -> list[str]:
    """Trace に残った Role の名前（データ品質担当など）を、キー（data_quality など）へ変える。未知の名前は無視する。"""
    return [_KEY_BY_NAME[n] for n in display_names if n in _KEY_BY_NAME]


def boundary_violations(tool_roles) -> list[str]:
    """Tool を、持たない Role が実行していないか。tool_roles は (Role の名前, Tool の名前) の並び（Trace の summary['tool_roles']）。

    Tool を持たせないことが権限の境界。委譲などで境界が破れていれば、そのことを日本語で返す。空なら守られている。
    """
    problems = []
    for role_name, tool in tool_roles:
        key = _KEY_BY_NAME.get(role_name)
        if key is None or tool not in ROLE_TOOLS[key]:
            problems.append(f"{role_name} が、持たない Tool を実行しました: {tool}")
    return problems
