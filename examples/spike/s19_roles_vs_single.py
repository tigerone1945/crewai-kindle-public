"""Spike 19: 第11章の実測。Case 07 の質問を、単一の Agent と、3つの Role で比べる（品質・Coordination Cost）。

どちらも、同じ Schema・同じ Guardrail（数値の検査）・同じ再試行（作り直し最大3回）を通る。違いは、責務を分けたかどうかだけ。
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai import Agent, Crew, Process, Task

import shared.analysis_service as service
from evaluations.checks import check_expected
from shared.roles import QUALITY_TOOLS, boundary_violations, role_keys
from shared.sales_data import get_llm
from shared.sales_tools import SALES_TOOLS
from shared.schemas import SalesAnalysisResult
from shared.tracing import trace_run

QUESTION = "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
QUALITY_WORDS = ("品質", "欠損", "重複", "整合", "キャンセル", "要レビュー")


def build_single(prompt, guardrail=None, guardrail_max_retries=0):
    agent = Agent(
        role="売上分析アシスタント", goal="データ品質の確認・売上傾向の分析・業務上の示唆を、Toolの結果だけを根拠にまとめる",
        backstory="数値は自分で計算せず、必ずToolの結果をそのまま使う。入力にない数値や事実を作らない。",
        tools=[*QUALITY_TOOLS, *SALES_TOOLS], llm=get_llm(), allow_delegation=False)
    task = Task(
        name="single_agent_analysis",
        description=(f"質問: {prompt}\n\nanalysis_type は integrated_analysis にします。metrics にはToolの結果から引用した数値だけを入れます。"
                     "insights は2〜4件、recommendations は1〜3件です。文章に数値を書くときは、Toolの結果の値をそのまま使い、"
                     "合計・差・比率を新たに計算したり、丸めたりしません。複数の構成比を足して「上位2つで○%」のようにも書きません。"),
        expected_output="SalesAnalysisResult の形式の統合分析", agent=agent, output_pydantic=SalesAnalysisResult,
        guardrail=guardrail, guardrail_max_retries=guardrail_max_retries)
    return Crew(agents=[agent], tasks=[task], process=Process.sequential)


def run(label, runner):
    rows = []
    for i in range(N):
        with trace_run() as t:
            try:
                result = runner()
                ok = True
            except service.AnalysisError:
                result, ok = None, False
        s = t.summary()
        text = ""
        if result:
            text = " ".join([result.summary, *result.insights, *result.recommendations, *(f"{m.label} {m.value:,.0f}" for m in result.metrics)])
        roles = role_keys(s["roles"])
        fails = check_expected({"must_contain_numbers_any_of": [28298695]}, text, [], roles) if ok else ["failed"]
        rows.append(dict(
            ok=ok, first_pass=s["attempts"] <= 1, tokens=s["prompt_tokens"] + s["completion_tokens"], ms=s["duration_ms"],
            dq_tool="check_data_quality" in s["tool_calls"], mentions_quality=any(w in text for w in QUALITY_WORDS),
            violations=len(boundary_violations(s["tool_roles"])), case07_ok=not fails))
        print(label, i + 1, rows[-1], flush=True)
    return rows


def summarize(label, rows):
    n = len(rows)
    avg = lambda k: round(statistics.mean(r[k] for r in rows))  # noqa: E731
    print(f"SUMMARY {label}: n={n} success={sum(r['ok'] for r in rows)} first_pass={sum(r['first_pass'] for r in rows)} "
          f"dq_tool_called={sum(r['dq_tool'] for r in rows)} mentions_quality={sum(r['mentions_quality'] for r in rows)} "
          f"boundary_violations={sum(r['violations'] for r in rows)} case07_ok={sum(r['case07_ok'] for r in rows)} "
          f"avg_tokens={avg('tokens')} avg_ms={avg('ms')}", flush=True)


which = sys.argv[2] if len(sys.argv) > 2 else "both"  # both / single / roles
if which in ("both", "single"):
    summarize("single", run("single", lambda: service._run_guarded(lambda p, g, r: build_single(p, g.check, r), QUESTION, 3, 0)))
if which in ("both", "roles"):
    summarize("roles", run("roles ", lambda: service.run_multi_role_analysis(QUESTION)))
