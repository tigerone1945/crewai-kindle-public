"""Spike 25: 第17章の実測。本物の Crew・LLM で、各層に故障を入れ、観測が、根本の層を言い当てるか確かめる。

故障の入れ方（本番の API と同じ経路: HTTP → Flow → Crew → Agent → Tool）:
  normal        故障なし
  input         範囲外の質問（入力の検査で止まる）
  tool_error    Tool が例外を出す（DB に届かない想定）
  tool_slow     Tool が 4 秒かかる（遅さの原因が、どこにあるか）
  llm_error     LLM の API キーが無効
  validation    出力の検査が、必ず却下する
各シナリオの記録（RunRecord）が、期待の層と一致するかを表にする。あわせて、観測自体のコスト（記録・集計の時間）を測る。
使い方: s25_failure_injection.py [回数] [シナリオ名,...]
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

import shared.alerts as alerts
import shared.api as api
import shared.guardrails as guardrails
import shared.sales_db as sales_db
from shared.memory_policy import PreferenceStore
from shared.observability import Observer, RunRecord
from shared.review_requests import ReviewStore
from tests.fakes import FakeMemory

QUESTION = "地域別の売上を比較してください。"
OUT_OF_SCOPE = "今日の東京の天気を教えてください。"


def make_client(tmp: Path):
    deps = api.Dependencies(review_store=ReviewStore(tmp / "r.db"), flow_db=tmp / "f.db", trace_dir=tmp / "t",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), observer=Observer())
    return deps, TestClient(api.create_app(deps), raise_server_exceptions=False)


class Inject:
    """故障を入れて、抜けるときに必ず戻す。"""

    def __init__(self, kind: str) -> None:
        self.kind, self._undo = kind, []

    def __enter__(self):
        if self.kind == "tool_error":
            self._patch(sales_db.SalesRepository, "sales_by", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("database is unreachable")))
        elif self.kind == "tool_slow":
            real = sales_db.SalesRepository.sales_by
            self._patch(sales_db.SalesRepository, "sales_by", lambda self, *a, **k: (time.sleep(4), real(self, *a, **k))[1])
        elif self.kind == "llm_error":
            self._undo.append(("env", "OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY")))
            os.environ["OPENAI_API_KEY"] = "sk-invalid-key-for-the-spike"
        elif self.kind == "validation":
            self._patch(guardrails.OutputGuard, "_inspect", lambda self, output: ("injected", "検査で必ず却下する（Spike）", None))
        return self

    def _patch(self, obj, name, value):
        self._undo.append(("attr", obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def __exit__(self, *exc):
        for item in reversed(self._undo):
            if item[0] == "attr":
                setattr(item[1], item[2], item[3])
            elif item[2] is None:
                os.environ.pop(item[1], None)
            else:
                os.environ[item[1]] = item[2]


SCENARIOS = [  # (名前, 質問, 期待する失敗の層)
    ("normal", QUESTION, ""), ("input", OUT_OF_SCOPE, "input"), ("tool_error", QUESTION, "tool"),
    ("tool_slow", QUESTION, ""), ("llm_error", QUESTION, "llm"), ("validation", QUESTION, "output_validation"),
]


def run_scenarios(n: int, only: set[str] | None = None) -> list[tuple]:
    rows = []
    for name, question, expected in SCENARIOS:
        if only and name not in only:
            continue
        for i in range(n):
            with tempfile.TemporaryDirectory() as d, Inject(name):
                deps, client = make_client(Path(d))
                t0 = time.time()
                r = client.post("/v1/analyses", json={"question": question, "actor_id": "user-001"})
                wall = time.time() - t0
                rec: RunRecord = deps.observer.runs_since(0)[-1]
                slow_tool = ""
                if name == "tool_slow":
                    import json
                    trace = json.loads((Path(d) / "t" / f"{rec.run_id}.json").read_text())
                    sums = {k: sum(e["duration_ms"] or 0 for e in trace["events"] if e["kind"] == k) / 1000 for k in ("tool", "check", "llm")}
                    slow_tool = f"Tool {sums['tool']:.1f}s / 検査 {sums['check']:.1f}s / LLM {sums['llm']:.1f}s / 全体 {rec.duration_ms / 1000:.1f}s"
                rows.append((name, expected, rec.failure_layer, r.status_code, rec.outcome, rec.attempts, rec.llm_calls,
                             len(rec.tool_calls), rec.tool_errors, round(wall, 1), slow_tool))
                print(rows[-1], flush=True)
    return rows


def observation_cost() -> None:
    o = Observer(known_tools={"get_sales_by_region"})
    base = RunRecord(run_id="r", ts=time.time(), route="single", outcome="analysis", status="ok", duration_ms=5000, attempts=1,
                     llm_calls=3, tool_calls=("get_sales_by_region",) * 2, prompt_tokens=900, completion_tokens=200)
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        times = []
        for _ in range(1000):
            t = time.perf_counter()
            o.record_run(base)
            times.append((time.perf_counter() - t) * 1000)
        w = alerts.Window(o.runs_since(0), [])
        t = time.perf_counter()
        for _ in range(100):
            alerts.evaluate(w)
        evaluate_ms = (time.perf_counter() - t) * 10
        t = time.perf_counter()
        for _ in range(100):
            o.render()
        render_ms = (time.perf_counter() - t) * 10
    print(f"\n観測のコスト: record_run 中央値 {statistics.median(times):.3f} ms / 最大 {max(times):.2f} ms, "
          f"evaluate（窓 1000 件）{evaluate_ms:.2f} ms, /metrics の生成 {render_ms:.2f} ms")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    rows = run_scenarios(n, set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None)
    print("\nシナリオ | 期待の層 | 判定の層 | HTTP | 結果 | 試行 | LLM | Tool | Toolエラー | 秒 | 備考")
    ok = 0
    for r in rows:
        print(" | ".join(map(str, r)), "" if r[1] == r[2] else "  ← 不一致")
        ok += r[1] == r[2]
    print(f"\n層の判定が期待と一致: {ok}/{len(rows)}")
    observation_cost()
