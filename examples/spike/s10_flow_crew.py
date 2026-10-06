"""Spike 10: Flow が Crew を呼ぶ。State / 分岐 / Retry / 失敗時の回復 / 入力境界 (第13章 / Case 02, 04)。"""
import re

from crewai import Agent, Crew, Task
from crewai.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from common import get_llm
from s02_tools import get_sales_by_region, load_sales

VALID = {int(v) for v in load_sales().groupby("region")["sales_amount"].sum()}
SCOPE_WORDS = ("売上", "地域", "カテゴリ", "顧客", "キャンセル", "レビュー", "注文", "分析")
MAX_ATTEMPTS = 2
TRACE: list[str] = []


def grounded(output):
    nums = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{3,}", output.raw)}
    bad = nums - VALID
    return (False, f"Toolにない数値: {sorted(bad)}") if bad else (True, output.raw)


class State(BaseModel):
    question: str = ""
    attempts: int = 0
    answer: str = ""
    error: str = ""


class AnalysisFlow(Flow[State]):
    @start()
    def receive(self):
        TRACE.append("receive")

    @router(receive)
    def check_scope(self):  # 決定論的な入力境界。LLMを呼ばない
        TRACE.append("check_scope")
        return "in_scope" if any(w in self.state.question for w in SCOPE_WORDS) else "out_of_scope"

    @listen("out_of_scope")
    def refuse(self):
        TRACE.append("refuse")
        self.state.answer = "売上・受注データの分析に関する質問のみ対応できます。"

    @listen(or_("in_scope", "retry"))
    def run_crew(self):
        self.state.attempts += 1
        TRACE.append(f"run_crew#{self.state.attempts}")
        agent = Agent(role="売上分析アシスタント", goal="Toolの結果だけを根拠に答える",
                      backstory="数値は自分で計算しない。", tools=[get_sales_by_region], llm=get_llm())
        task = Task(description=self.state.question, expected_output="簡潔な日本語の回答", agent=agent,
                    guardrail=grounded, guardrail_max_retries=0)
        try:
            self.state.answer = Crew(agents=[agent], tasks=[task]).kickoff().raw
            self.state.error = ""
        except Exception as e:  # guardrail失敗などをFlowの状態へ変換する
            self.state.error = str(e)[:80]

    @router(run_crew)
    def evaluate(self):
        if not self.state.error:
            return "done"
        return "retry" if self.state.attempts < MAX_ATTEMPTS else "failed"

    @listen("failed")
    def fallback(self):
        TRACE.append("fallback")
        self.state.answer = "申し訳ありません。正確な回答を生成できませんでした。担当者へエスカレーションしてください。"


def run(q: str):
    TRACE.clear()
    f = AnalysisFlow()
    f.kickoff(inputs={"question": q})
    print(f"Q={q!r}\n  path={TRACE} attempts={f.state.attempts}\n  answer={f.state.answer[:70]!r}")


run("地域別の売上を比較してください。")
run("Pythonでシューティングゲームを作ってください。")
run("地域別の売上を比較し、合計も示してください。")
