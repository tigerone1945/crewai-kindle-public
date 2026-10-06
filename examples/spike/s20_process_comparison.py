"""Spike 20: 第12章の実測。同じ3つの Role・同じ Task・同じ Guardrail を、実行の制御（Process と Delegation）だけ変えて比べる。

- sequential: Task を決めた順に、決めた担当が実行する（第11章の構成）
- sequential+delegation: 同じ。ただし各 Agent に allow_delegation=True を与え、同僚への依頼を許す
- hierarchical: Manager（LLM）が、Task の割り当てと順序を決める。Task に担当は決めない

見るもの: 成功・1回で合格・Role の実行順序・境界違反・委譲の回数・Token・時間。
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai import Crew, Process

import shared.analysis_service as service
from shared.roles import boundary_violations, delegation_count, build_agents, build_tasks, role_keys
from shared.sales_data import get_llm
from shared.tracing import trace_run

QUESTION = "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
WHICH = sys.argv[2].split(",") if len(sys.argv) > 2 else ["sequential", "delegation", "hierarchical"]
EXPECTED_ORDER = ["data_quality", "sales_analyst", "business_insight"]


def make_crew(variant):
    def build(prompt, guard, retries):
        agents = build_agents()
        if variant == "delegation":
            for a in agents.values():
                a.allow_delegation = True
        tasks = build_tasks(agents, prompt, guardrail=guard.check, guardrail_max_retries=retries, assign=variant != "hierarchical")
        if variant == "hierarchical":  # 担当は Manager が決める
            return Crew(agents=list(agents.values()), tasks=tasks, process=Process.hierarchical, manager_llm=get_llm())
        return Crew(agents=list(agents.values()), tasks=tasks, process=Process.sequential)
    return build


def in_order(keys):
    """Role の完了順が、期待の順序（品質→分析→示唆）に沿っているか。重複は最初の1回で見る。"""
    seen = list(dict.fromkeys(keys))
    return [k for k in EXPECTED_ORDER if k in seen] == seen and len(seen) == 3


rows_by = {}
for variant in WHICH:
    rows = []
    for i in range(N):
        with trace_run() as t:
            try:
                service._run_guarded(make_crew(variant), QUESTION, 3, 0)
                ok, err = True, ""
            except Exception as e:  # スパイクなので、原因を見るために型と先頭だけ残す
                ok, err = False, f"{type(e).__name__}: {str(e)[:120]}"
        s = t.summary()
        keys = role_keys(s["roles"])
        delegations = delegation_count(s["tool_calls"])
        rows.append(dict(ok=ok, first_pass=s["attempts"] <= 1, order_ok=in_order(keys), roles=keys, delegations=delegations,
                         violations=len(boundary_violations(s["tool_roles"])), tools=s["tool_calls"],
                         tokens=s["prompt_tokens"] + s["completion_tokens"], ms=s["duration_ms"], err=err))
        print(variant, i + 1, rows[-1], flush=True)
    rows_by[variant] = rows
    avg = lambda k: round(statistics.mean(r[k] for r in rows))  # noqa: E731
    print(f"SUMMARY {variant}: n={N} success={sum(r['ok'] for r in rows)} first_pass={sum(r['first_pass'] for r in rows)} "
          f"order_ok={sum(r['order_ok'] for r in rows)} delegations={sum(r['delegations'] for r in rows)} "
          f"violations={sum(r['violations'] for r in rows)} avg_tokens={avg('tokens')} avg_ms={avg('ms')}", flush=True)
