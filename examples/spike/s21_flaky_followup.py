"""Spike 21: test_case03_follow_up_uses_conversation_context が確率的に失敗する原因の調査。

2つの呼び出し（最初の質問 / 会話の続き）それぞれで、検査の却下の回数と理由（Trace の attempt イベント）を記録する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service
from shared.conversation import Turn
from shared.tracing import trace_run

QUESTION = {"region": "地域別の売上を分析してください。", "category": "カテゴリ別の売上を分析してください。",
            "product": "商品別の売上を分析してください。"}[sys.argv[3] if len(sys.argv) > 3 else "region"]
FOLLOW = "では一番高かった地域は？"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
FIRST_ONLY = len(sys.argv) > 2 and sys.argv[2] == "first-only"


def call(label, question, **kw):
    with trace_run() as t:
        try:
            result, ok = service.run_analysis(question, **kw), True
        except service.AnalysisError:
            result, ok = None, False
    rejects = [e.attrs.get("error", "")[:90] for e in t.events if e.kind == "attempt" and e.status == "error"]
    print(f"{label} ok={ok} rejects={len(rejects)} {rejects}", flush=True)
    return result


stats = {"first": [0, 0], "follow": [0, 0]}
for i in range(N):
    first = call(f"[{i + 1}] first ", QUESTION)
    stats["first"][0] += first is not None
    stats["first"][1] += 1
    if first is None or FIRST_ONLY:
        continue
    r = call(f"[{i + 1}] follow", FOLLOW, history=[Turn.from_result(QUESTION, first)])
    stats["follow"][0] += r is not None
    stats["follow"][1] += 1
print("SUMMARY", stats, flush=True)
