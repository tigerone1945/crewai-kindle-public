"""Spike 8-10 共通: 3 Role の Agent / Task 定義（第11章 Role設計）。"""
from crewai import Agent, Task
from crewai.tools import tool

from common import get_llm, load_sales
from s02_tools import get_sales_by_region, get_sales_summary

QUESTION = "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。"


@tool("check_data_quality")
def check_data_quality() -> str:
    """売上データの品質（欠損・重複・キャンセル注文の売上額・金額の整合性）を確認する。"""
    df = load_sales()
    cancelled = df[df["status"] == "キャンセル"]
    return (
        f"レコード数: {len(df)}\n欠損値の総数: {int(df.isna().sum().sum())}\n"
        f"order_id重複: {int(df['order_id'].duplicated().sum())}件\n"
        f"キャンセル{len(cancelled)}件のうちsales_amountが0でないもの: {int((cancelled['sales_amount'] != 0).sum())}件\n"
        f"sales_amountがtotal_amountを超える注文: {int((df['sales_amount'] > df['total_amount']).sum())}件"
    )


def build_agents():
    dq = Agent(role="データ品質担当", goal="分析前にデータの品質問題の有無を確認し、結果を事実だけで報告する",
               backstory="欠損・重複・整合性の確認だけを担当する。売上の分析や示唆は述べない。",
               tools=[check_data_quality], llm=get_llm(), allow_delegation=False)
    an = Agent(role="売上分析担当", goal="Toolの結果だけを根拠に売上傾向を数値で報告する",
               backstory="数値は自分で計算せずToolを使う。業務上の解釈や提言は述べない。",
               tools=[get_sales_summary, get_sales_by_region], llm=get_llm(), allow_delegation=False)
    bi = Agent(role="業務示唆担当", goal="前工程の結果から業務上の示唆を整理する",
               backstory="入力にない数値や事実を作らない。Toolは持たず、前工程の結果だけを使う。",
               llm=get_llm(), allow_delegation=False)
    return dq, an, bi


def build_tasks(dq, an, bi):
    t1 = Task(description="売上データの品質を確認してください。", expected_output="品質確認結果（問題の有無と件数）", agent=dq)
    t2 = Task(description="売上の全体状況と地域別の傾向を数値で報告してください。", expected_output="売上全体と地域別の数値",
              agent=an, context=[t1])
    t3 = Task(description="品質確認と売上分析の結果から、業務上の示唆（発見・注意点・次の確認事項）をまとめてください。",
              expected_output="発見・注意点・次の確認事項。入力にない数値は使わない", agent=bi, context=[t1, t2])
    return t1, t2, t3
