"""Spike 26: 第18章の実測。
A. ゲートの検出力: 真の合格率が違う Case を、n 回実行したとき、FAIL と判定される確率（二項分布の計算。LLM は呼ばない）
B. 劣化の注入: 本物の Crew・LLM で、わざと壊し、ゲートが、見つけるかを確かめる（基準 evaluations/baseline.json と比べる）
   tool_format     Tool の出力から「上位からの累積」を消す（第12章の修正を戻す）
   routing         経路の規則から「品質」を外す
   tool_description  get_sales_by_region の説明を、別の内容にする
C. 審査員（LLM-as-a-Judge）: 同じ回答を5回採点したときの安定性と、劣化させた回答への識別力
使い方: s26_regression_gate.py [A|B|C|ABC] [回数]
"""
import copy
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_flow as analysis_flow
import shared.sales_tools as sales_tools
from evaluations.gate import compare, fail_probability, load_baseline
from evaluations.judge import judge
from evaluations.runner import Harness, evaluate_case, load_cases
from shared.schemas import SalesAnalysisResult


def part_a() -> None:
    print("A. ゲートの検出力（基準 0.8）: 真の合格率ごとに、FAIL と判定される確率")
    rates = (0.95, 0.8, 0.6, 0.4, 0.2)
    print("n \\ 真の合格率 | " + " | ".join(f"{r:.2f}" for r in rates))
    for n in (3, 5, 10, 20):
        print(f"{n:>3} | " + " | ".join(f"{fail_probability(n, r, 0.8):.2f}" for r in rates))


class Inject:
    def __init__(self, name):
        self.name, self._undo = name, []

    def __enter__(self):
        if self.name == "tool_format":
            self._set(sales_tools, "_format", lambda rows, column: "\n".join(f"{r[column]}: {r['sales']:,}円（構成比 {r['share_pct']}%）" for r in rows))
        elif self.name == "routing":
            self._set(analysis_flow, "INTEGRATED_WORDS", ())
        elif self.name == "tool_description":
            t = sales_tools.get_sales_by_region
            self._set(t, "description", "顧客の電話番号と住所を返す。売上の集計には使わない。")
        return self

    def _set(self, obj, attr, value):
        self._undo.append((obj, attr, getattr(obj, attr)))
        try:
            setattr(obj, attr, value)
        except Exception:
            object.__setattr__(obj, attr, value)

    def __exit__(self, *exc):
        for obj, attr, old in reversed(self._undo):
            try:
                setattr(obj, attr, old)
            except Exception:
                object.__setattr__(obj, attr, old)


INJECTIONS = {
    "tool_format": ["case-f001a-cumulative-share-region", "case-f001b-cumulative-share-category", "case-f001c-cumulative-share-product"],
    "routing": ["case-c03-flow-branch", "case-07-multi-agent"],
    "tool_description": ["case-02-region-comparison", "case-01-overall-sales"],
}


def part_b(runs: int) -> None:
    baseline = load_baseline()
    cases = {c["id"]: c for c in load_cases()}
    for name, ids in INJECTIONS.items():
        print(f"\nB. 劣化の注入: {name}（各 Case {runs} 回）")
        with Inject(name):
            results = [evaluate_case(cases[i], Harness(), runs) for i in ids]
        for r in results:
            print(f"  {r['id']}: {r['passes']}/{r['n']}  tokens={r['mean_tokens']}  {r['failures'][:1]}")
        for f in compare(baseline, results):
            if f.case in ids and f.level in ("FAIL", "WARN"):
                print(f"  → {f.level} {f.case}: {f.message[:160]}")


def part_c(runs: int) -> None:
    cases = {c["id"]: c for c in load_cases()}
    case = cases["case-02-region-comparison"]
    harness = Harness()
    good: list[SalesAnalysisResult] = []
    for _ in range(3):
        obs = harness.observe(case)
        if obs.final:
            good.append(obs.final)
    q = case["turns"][0]
    variants = {"original": lambda r: r,
                "generic_recommendations": lambda r: r.model_copy(update={"recommendations": ["データを確認する"]}),
                "contradicting_summary": lambda r: r.model_copy(update={"summary": "最も売上が低いのは東北で、最も高いのは中部です。", "insights": ["中部が最も売上が高い", "東北は最も売上が低い"]})}
    print(f"\nC. 審査員: {len(good)} 件の回答 × 3 種類 × {runs} 回採点")
    for name, make in variants.items():
        scores, per_criterion = [], {"actionable": 0, "specific": 0, "consistent": 0}
        spread = []
        for g in good:
            marks = [judge(q, make(g)) for _ in range(runs)]
            s = [m.score for m in marks]
            scores += s
            spread.append(max(s) - min(s))
            for m in marks:
                for k in per_criterion:
                    per_criterion[k] += getattr(m, k)
        total = len(scores)
        print(f"  {name:<26} 平均 {statistics.mean(scores):.2f}/3  同じ回答の採点の幅（最大-最小）の平均 {statistics.mean(spread):.2f}  "
              + " ".join(f"{k}={v}/{total}" for k, v in per_criterion.items()))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "ABC"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    if "A" in which:
        part_a()
    if "B" in which:
        part_b(runs)
    if "C" in which:
        part_c(runs)
