"""運用の記録（実行ごとの JSON 行）を集計して、状況と、基準を超えているものを表示する（第17章）。

入力は、サービスの標準出力（run_completed の行）。それ以外の行（アクセスの記録など）は、読み飛ばす。
  docker compose -f docker-compose.prod.yml logs --no-log-prefix api | uv run python chapters/ch17-observability/ops_report.py
  uv run python chapters/ch17-observability/ops_report.py run.log --since-minutes 60

Metric は、サービスのメモリの中で、再起動で消える。ログは、残る。「先週の火曜に、何が起きたか」は、ログから集計する。
質問・回答は、ログにない。見られるのは、件数・時間・Token・失敗の層だけ。
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.alerts import DEFAULT_RULES, Window, evaluate, measure, percentile  # noqa: E402
from shared.observability import RunRecord  # noqa: E402


def read_runs(lines, *, since: float = 0.0) -> tuple[list[RunRecord], int]:
    """run_completed の行を RunRecord にする。(実行の記録, 読めなかった行の数)。run_completed に見える行が壊れていたら、数える。"""
    runs, skipped = [], 0
    for line in lines:
        line = line.strip()
        if not line.startswith("{") or '"run_completed"' not in line:
            continue
        try:
            record = RunRecord.from_log(json.loads(line))
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
        if record.ts >= since:
            runs.append(record)
    return runs, skipped


def report(runs: list[RunRecord], skipped: int = 0) -> str:
    if not runs:
        return "対象の実行がありません。" + (f"（読めない行: {skipped}）" if skipped else "")
    out = [f"実行: {len(runs)} 件" + (f"（読めない行 {skipped} 件を除く）" if skipped else "")]
    out.append("結果: " + "、".join(f"{k} {v}" for k, v in Counter(r.outcome for r in runs).most_common()))
    layers = Counter(r.failure_layer for r in runs if r.failure_layer)
    if layers:
        out.append("失敗の層（根本）: " + "、".join(f"{k} {v}" for k, v in layers.most_common()))
    worked = [r for r in runs if r.outcome in ("analysis", "escalated", "error")]
    if worked:
        durations = [r.duration_ms / 1000 for r in worked]
        out.append(f"所要時間: 中央値 {percentile(durations, 50):.1f} 秒 / p95 {percentile(durations, 95):.1f} 秒 / 最大 {max(durations):.1f} 秒")
    answered = [r for r in runs if r.answered]
    if answered:
        out.append(f"回答した実行の平均 Token: {sum(r.total_tokens for r in answered) / len(answered):,.0f}")
    tools = Counter(t for r in runs for t in r.tool_calls)
    if tools:
        out.append("Tool: " + "、".join(f"{k} {v}" for k, v in tools.most_common()))
    out.append("指標: " + "、".join(f"{k}={v['value']}(n={v['samples']})" for k, v in measure(Window(runs, [])).items() if v["value"] is not None))
    fired = evaluate(Window(runs, []), DEFAULT_RULES)
    out.append("基準を超えている: " + ("なし" if not fired else ""))
    out.extend(f"  [{a.layer}] {a.rule}: {a.value:.3f}（基準 {a.threshold}、n={a.samples}）" for a in fired)
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", nargs="?", help="ログのファイル。省略なら標準入力")
    parser.add_argument("--since-minutes", type=float, default=None, help="直近の何分だけを集計するか")
    args = parser.parse_args()
    lines = Path(args.file).read_text(encoding="utf-8").splitlines() if args.file else sys.stdin
    since = time.time() - args.since_minutes * 60 if args.since_minutes else 0.0
    runs, skipped = read_runs(lines, since=since)
    print(report(runs, skipped))


if __name__ == "__main__":
    main()
