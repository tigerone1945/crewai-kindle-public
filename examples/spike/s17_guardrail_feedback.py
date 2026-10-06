"""Spike 17: Task Guardrail の再生成が効かないケース（好み「結論:」あり）の失敗率と、却下理由の書き方による差を測る。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service
from shared.tracing import trace_run

PREFS = [] if (len(sys.argv) > 2 and sys.argv[2] == "nopref") else ["要約は必ず「結論:」で始める"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
MAX_ATTEMPTS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
TASK_RETRIES = int(sys.argv[4]) if len(sys.argv) > 4 else 0
ok = retried = exhausted = 0
for i in range(N):
    with trace_run() as t:
        try:
            r = service.run_analysis("地域別の売上を分析してください。", preferences=PREFS, max_attempts=MAX_ATTEMPTS, task_retries=TASK_RETRIES)
            outcome = "ok"
        except service.AnalysisError as e:
            outcome, r, err = "exhausted", None, str(e)
    s = t.summary()
    ok += outcome == "ok"
    exhausted += outcome == "exhausted"
    retried += s["attempts"] > 1
    print(i + 1, outcome, "attempts", s["attempts"], "tools", len(s["tool_calls"]), "ms", s["duration_ms"], "tok", s["prompt_tokens"] + s["completion_tokens"], "summary:", (r.summary[:50] if r else err[-80:]))
print(f"max_attempts={MAX_ATTEMPTS} task_retries={TASK_RETRIES}")
print(f"N={N} ok={ok} exhausted={exhausted} runs_with_retry={retried}")
