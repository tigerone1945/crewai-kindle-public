"""Regression の判定（第18章）: 評価の結果を、基準（baseline.json）と比べて、デプロイしてよいかを決める。

LLM の出力は確率的で、同じ Case でも、毎回の合否が変わる。数回の実行で、「合格率が下がった」と言い切れるかは、実行の回数による。
このため、判定を3つに分ける。

- FAIL  合格率が、基準を下回っていると、統計的に言える（合格率の信頼区間の上限が、基準未満）。デプロイを止める
- WARN  合格率の見かけは基準未満だが、回数が少なく、偶然の範囲（上限は、基準以上）。回数を増やして、確かめる
- OK    基準以上

LLM を呼ばない Case（決定的）は、1回でも失敗したら FAIL。偶然がない。
基準（min_pass_rate）は、人が決める。測った合格率から決めて、baseline.json へ書く（決定的な Case は 1.0、LLM を呼ぶ Case の既定は 0.8）。
信頼区間は、Wilson 区間（回数が少なくても、0%・100% の端で破綻しない）。
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"
DEFAULT_MIN_PASS_RATE = 0.8
Z95 = 1.96
TOKENS_WARN, TOKENS_FAIL = 1.5, 2.0  # 基準の平均 Token の何倍で、警告・失敗か
LATENCY_WARN, LATENCY_MIN_SECONDS = 2.0, 5.0  # 基準の所要時間の中央値の何倍（かつ何秒以上）で、警告か


def wilson_bounds(passes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = passes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(center - margin, 0.0), min(center + margin, 1.0)


def fail_probability(n: int, true_rate: float, required: float) -> float:
    """真の合格率が true_rate の Case を n 回実行したとき、FAIL と判定される確率（二項分布）。ゲートの検出力と、誤警報の見積もり。"""
    total = 0.0
    for k in range(n + 1):
        if wilson_bounds(k, n)[1] < required:
            total += math.comb(n, k) * true_rate**k * (1 - true_rate) ** (n - k)
    return total


@dataclass(frozen=True)
class Finding:
    level: str  # FAIL / WARN / INFO
    case: str
    message: str


def make_baseline(results: list[dict], meta: dict, previous: dict | None = None) -> dict:
    """測った結果から、基準を作る。min_pass_rate は、前の基準があれば引き継ぐ（人が決めた値を、測定のたびに書き換えない）。"""
    cases = {}
    for r in results:
        old = (previous or {}).get("cases", {}).get(r["id"], {})
        default = 1.0 if r["deterministic"] else DEFAULT_MIN_PASS_RATE
        cases[r["id"]] = {"n": r["n"], "passes": r["passes"], "deterministic": r["deterministic"],
                          "min_pass_rate": old.get("min_pass_rate", default),
                          "mean_tokens": r["mean_tokens"], "median_seconds": r["median_seconds"]}
    return {"meta": meta, "cases": cases}


def compare(baseline: dict, results: list[dict]) -> list[Finding]:
    findings = []
    base_cases = baseline.get("cases", {})
    for r in results:
        base = base_cases.get(r["id"])
        required = base["min_pass_rate"] if base else (1.0 if r["deterministic"] else DEFAULT_MIN_PASS_RATE)
        if base is None:
            findings.append(Finding("INFO", r["id"], "基準にない Case です（既定の基準を使います）。--update-baseline で、基準へ加えます"))
        lower, upper = wilson_bounds(r["passes"], r["n"])
        rate = r["passes"] / r["n"]
        if r["deterministic"]:
            if r["passes"] < r["n"]:
                findings.append(Finding("FAIL", r["id"], f"決定的な Case が失敗しました（{r['passes']}/{r['n']}）: {_first(r)}"))
        elif upper < required:
            findings.append(Finding("FAIL", r["id"], f"合格率 {r['passes']}/{r['n']}（95% 区間 {lower:.2f}〜{upper:.2f}）は、基準 {required:.2f} を下回っています: {_first(r)}"))
        elif rate < required:
            findings.append(Finding("WARN", r["id"], f"合格率 {r['passes']}/{r['n']} は、基準 {required:.2f} 未満ですが、回数が少なく、偶然の範囲です（上限 {upper:.2f}）。回数を増やして確かめてください: {_first(r)}"))
        if base and base["mean_tokens"] and r["mean_tokens"]:
            ratio = r["mean_tokens"] / base["mean_tokens"]
            if ratio > TOKENS_FAIL:
                findings.append(Finding("FAIL", r["id"], f"平均 Token が基準の {ratio:.1f} 倍です（{base['mean_tokens']} → {r['mean_tokens']}）"))
            elif ratio > TOKENS_WARN:
                findings.append(Finding("WARN", r["id"], f"平均 Token が基準の {ratio:.1f} 倍です（{base['mean_tokens']} → {r['mean_tokens']}）"))
        if base and base["median_seconds"] and r["median_seconds"] > max(base["median_seconds"] * LATENCY_WARN, LATENCY_MIN_SECONDS):
            findings.append(Finding("WARN", r["id"], f"所要時間の中央値が基準の {r['median_seconds'] / base['median_seconds']:.1f} 倍です（{base['median_seconds']:.1f} → {r['median_seconds']:.1f} 秒）"))
        for limit in r.get("known_limits", []):
            if limit["resolved"]:
                findings.append(Finding("INFO", r["id"], f"既知の限界が解消されました。Case へ移してください: {limit['question']}"))
    ran = {r["id"] for r in results}
    findings += [Finding("INFO", cid, "基準にあるが、今回は実行していません") for cid in base_cases if cid not in ran]
    return findings


def _first(r: dict) -> str:
    return r["failures"][0] if r["failures"] else ""


def passed(findings: list[Finding]) -> bool:
    return not any(f.level == "FAIL" for f in findings)


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"基準がありません: {path}。--update-baseline で作ります")
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(baseline: dict, path: Path = BASELINE_PATH) -> None:
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
