"""1回の実行の Trace を、時系列で表示する（第17章）。ログで「この run_id が遅い・失敗した」と分かったあとに、中を見る。

  uv run python chapters/ch17-observability/inspect_run.py <run_id>
  uv run python chapters/ch17-observability/inspect_run.py <run_id> --dir data/traces

コンテナの中の Trace を見るとき:
  docker compose -f docker-compose.prod.yml exec api python chapters/ch17-observability/inspect_run.py <run_id> --dir /app/data/traces
表示するのは、種類・名前・状態・時間・件数だけ。Trace に、質問・Tool の中身は、もともと入っていない。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RUN_ID = re.compile(r"^[0-9a-f]{12}$")


def load_trace(run_id: str, directory: Path) -> dict:
    if not RUN_ID.match(run_id):  # パスの区切りを含む入力で、別のファイルを読ませない
        raise ValueError("run_id は、12桁の16進数です")
    path = directory / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Trace がありません: {run_id}（保存期間を過ぎて消えたか、別のコンテナで実行された可能性があります）")
    return json.loads(path.read_text(encoding="utf-8"))


def timeline(trace: dict) -> str:
    from shared.observability import classify_failure
    from shared.tracing import TraceEvent

    s = trace["summary"]
    events = [TraceEvent(e["kind"], e["name"], e["status"], e["at_ms"], e.get("duration_ms")) for e in trace["events"]]
    out = [f"run_id={s['run_id']}  status={s['status']}  所要 {(s['duration_ms'] or 0) / 1000:.1f} 秒  試行 {s['attempts']}  "
           f"LLM {s['llm_calls']} 回  Token {s['prompt_tokens'] + s['completion_tokens']:,}  エラー {s['errors']}"]
    errors = [e for e in events if e.status == "error"]
    if errors:
        first = classify_failure("escalated", "transient", events)
        if s["status"] == "ok":
            out.append(f"途中のエラー（回復して、最終的には成功）: {first}")
        else:
            out.append(f"最初のエラー: {first}（これより上の層のエラーは、結果として伝わったもの）")
    out.append("")
    out.append(f"{'終了':>8}  {'所要':>8}  {'種類':<9}{'状態':<8}名前")
    for raw, e in zip(trace["events"], events):
        took = f"{e.duration_ms / 1000:.2f}s" if e.duration_ms is not None else "-"
        extra = f"  ← {raw['error']}" if raw.get("error") else ""
        out.append(f"{e.at_ms / 1000:>7.2f}s  {took:>8}  {e.kind:<9}{e.status:<8}{e.name}{extra}")
    return "\n".join(out)


def main() -> None:
    from shared.tracing import TRACE_DIR

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--dir", type=Path, default=TRACE_DIR)
    args = parser.parse_args()
    try:
        print(timeline(load_trace(args.run_id, args.dir)))
    except (ValueError, FileNotFoundError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
