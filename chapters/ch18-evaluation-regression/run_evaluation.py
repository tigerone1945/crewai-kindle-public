"""Evaluation を実行し、基準と比べる（第18章）。

  uv run python chapters/ch18-evaluation-regression/run_evaluation.py                     # すべての Case を3回ずつ
  uv run python chapters/ch18-evaluation-regression/run_evaluation.py --runs 10 --gate    # 10回ずつ。基準を下回れば、終了コード 1（デプロイを止める）
  uv run python chapters/ch18-evaluation-regression/run_evaluation.py --cases case-c03,case-c04   # 決定的な Case だけ（LLM を呼ばない）
  uv run python chapters/ch18-evaluation-regression/run_evaluation.py --runs 5 --update-baseline  # 基準を更新（測った結果を、人が確かめてから）

LLM を呼ぶ Case は、APIキーが必要で、実行のたびに Token を使う。結果は data/evaluations/ に保存する（Git の対象外）。
"""
import argparse
import json
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluations.gate import compare, load_baseline, make_baseline, passed, save_baseline  # noqa: E402
from evaluations.runner import Harness, evaluate_case, load_cases  # noqa: E402
from shared.sales_data import MODEL, ROOT  # noqa: E402


def meta(runs: int) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    return {"commit": commit or "unknown", "model": MODEL, "crewai": version("crewai"), "runs": runs, "date": time.strftime("%Y-%m-%d")}


def render(results: list[dict], findings) -> str:
    out = [f"{'Case':<42}{'種類':<12}{'合格':>7}  {'平均Token':>9}  {'中央値秒':>8}"]
    for r in results:
        out.append(f"{r['id']:<42}{r['kind']:<12}{r['passes']:>3}/{r['n']:<3}  {r['mean_tokens']:>9,}  {r['median_seconds']:>8.1f}")
        for axis, (p, n) in r["axes"].items():
            if p < n:
                out.append(f"    ✗ {axis}: {p}/{n}")
    for r in results:
        for failure in r["failures"][:3]:
            out.append(f"  [{r['id']}] {failure}")
    out.append("")
    out += [f"{f.level:<5} {f.case}: {f.message}" for f in findings] or ["（指摘なし）"]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3, help="LLM を呼ぶ Case を、何回ずつ実行するか")
    parser.add_argument("--cases", help="Case の id（カンマ区切り）。省略なら、すべて")
    parser.add_argument("--gate", action="store_true", help="基準を下回れば、終了コード 1")
    parser.add_argument("--update-baseline", action="store_true", help="今回の結果で、基準を更新する（全 Case を実行したときだけ）")
    args = parser.parse_args()

    cases = load_cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"未知の Case: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in wanted]
    harness = Harness()
    results = []
    for case in cases:
        print(f"... {case['id']}", file=sys.stderr, flush=True)
        results.append(evaluate_case(case, harness, args.runs))

    run_meta = meta(args.runs)
    out_dir = ROOT / "data" / "evaluations"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps({"meta": run_meta, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.update_baseline:
        if args.cases:
            sys.exit("--update-baseline は、全 Case を実行したときだけ使えます（--cases と併用できません）")
        try:
            previous = load_baseline()
        except FileNotFoundError:
            previous = None
        save_baseline(make_baseline(results, run_meta, previous))
        print("基準を更新しました: evaluations/baseline.json（min_pass_rate は、前の値を引き継ぎます）", file=sys.stderr)
        return 0
    try:
        baseline = load_baseline()
    except FileNotFoundError as e:
        print(render(results, []))
        print(f"\n{e}", file=sys.stderr)
        return 1 if args.gate else 0
    findings = compare(baseline, results)
    print(render(results, findings))
    return 0 if passed(findings) or not args.gate else 1


if __name__ == "__main__":
    sys.exit(main())
