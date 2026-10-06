"""Spike 6: Task Guardrail (第7章 / Evaluation Case 02, 04)。

s03 で見た「Tool結果は正しいのに解釈が誤る」問題を、数値の突合で検出できるか検証する。
"""
import re

from crewai import Agent, Crew, Task

from common import get_llm
from s02_tools import CALLS, get_sales_by_region, load_sales

df = load_sales()
VALID = {int(v) for v in df.groupby("region")["sales_amount"].sum()}
ATTEMPTS: list[str] = []


def numbers_are_grounded(output):
    """出力に現れる4桁以上の数値が、Tool結果（地域別売上）に存在することを要求する。"""
    ATTEMPTS.append(output.raw[:60])
    nums = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{3,}", output.raw)}
    bad = nums - VALID
    if bad:
        return False, f"Toolの結果にない数値が含まれています: {sorted(bad)}。Toolの結果の数値だけを使ってください。"
    return True, output.raw


def run(question: str, guardrail=None):
    CALLS.clear(); ATTEMPTS.clear()
    agent = Agent(role="売上分析アシスタント", goal="Toolの結果だけを根拠に答える",
                  backstory="数値は自分で計算しない。売上分析以外の依頼は断る。",
                  tools=[get_sales_by_region], llm=get_llm())
    task = Task(description=question, expected_output="簡潔な日本語の回答", agent=agent,
                guardrail=guardrail, guardrail_max_retries=2)
    out = Crew(agents=[agent], tasks=[task]).kickoff()
    return out.raw


def attempt(label: str, question: str, guardrail=None):
    try:
        r = run(question, guardrail)
        status = "OK"
    except Exception as e:  # リトライ超過は汎用Exceptionで送出される
        r, status = f"{type(e).__name__}: {str(e)[:110]}", "FAILED"
    print(f"[{label}] {status} | attempts={len(ATTEMPTS)} tools={CALLS} | {r[:130]!r}")


attempt("1 根拠ある数値のみ", "地域別の売上を比較してください。", numbers_are_grounded)
attempt("2 合計を求める(LLMが自分で足す)", "地域別の売上を比較し、合計も示してください。", numbers_are_grounded)
attempt("3 Case04 範囲外(guardrailなし)", "Pythonでシューティングゲームを作ってください。")
