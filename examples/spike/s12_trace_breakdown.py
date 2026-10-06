"""Spike 12: 第6章の実測。所要時間の内訳（LLM / Tool / その他）を、5回の実行でTraceから集計する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service
from shared.tracing import trace_run

QUESTION = "地域別の売上を比較してください。"
rows = []
for i in range(5):
    with trace_run() as t:
        try:
            service.run_analysis(QUESTION, max_attempts=1)
        except service.AnalysisError as e:
            print(f"run {i + 1}: rejected ({str(e)[:60]})")
    ev = t.events
    s = t.summary()
    llm_ms = sum(e.duration_ms or 0 for e in ev if e.kind == "llm")
    tool_ms = sum(e.duration_ms or 0 for e in ev if e.kind == "tool")
    total = s["duration_ms"] or 0
    rows.append((total, llm_ms, tool_ms, s["llm_calls"], s["prompt_tokens"], s["completion_tokens"]))
    print(f"run {i + 1}: total={total}ms llm={llm_ms}ms tool={tool_ms}ms other={total - llm_ms - tool_ms}ms "
          f"llm_calls={s['llm_calls']} tokens={s['prompt_tokens']}/{s['completion_tokens']} status={s['status']}")
n = len(rows)
print("avg: total={:.0f} llm={:.0f} tool={:.0f} llm_share={:.0%}".format(
    sum(r[0] for r in rows) / n, sum(r[1] for r in rows) / n, sum(r[2] for r in rows) / n,
    sum(r[1] for r in rows) / max(1, sum(r[0] for r in rows))))
