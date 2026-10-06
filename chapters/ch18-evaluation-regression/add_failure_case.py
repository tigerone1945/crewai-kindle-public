"""見つけた失敗を、Evaluation Case として加える（第18章）。

  # 実行の記録（run_completed の1行）から、下書きを見る。質問は、空（記録には、質問がない）
  docker compose -f docker-compose.prod.yml logs --no-log-prefix api | grep <run_id> | uv run python chapters/ch18-evaluation-regression/add_failure_case.py draft

  # 再現できた質問と、正しい答えの条件を書いて、加える
  uv run python chapters/ch18-evaluation-regression/add_failure_case.py add --slug overall-explores-every-tool --title "..." \\
      --layer tool --question "売上全体の状況を教えてください。" --expected '{"tools_any_of": ["get_sales_summary"], "max_tool_calls": 2}' --notes "..."
質問に、秘密情報・個人情報が含まれると、加えられない（Case は、コミットされる）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluations.failure_cases import InvalidFailureCase, add_failure_case, skeleton_from_log  # noqa: E402
from evaluations.runner import load_cases  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("draft", help="標準入力の run_completed の行から、下書きを表示する")
    add = sub.add_parser("add")
    add.add_argument("--slug", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--layer", required=True)
    add.add_argument("--question", action="append", required=True, help="複数ターンなら、繰り返す")
    add.add_argument("--expected", required=True, help="JSON")
    add.add_argument("--notes", required=True)
    args = parser.parse_args()
    if args.command == "draft":
        for line in sys.stdin:
            if '"run_completed"' in line and line.lstrip().startswith("{"):
                print(json.dumps(skeleton_from_log(json.loads(line)), ensure_ascii=False, indent=2))
        return
    try:
        case = add_failure_case(args.question, json.loads(args.expected), layer=args.layer, notes=args.notes, slug=args.slug, title=args.title,
                                existing_ids=[c["id"] for c in load_cases()])
    except (InvalidFailureCase, json.JSONDecodeError) as e:
        sys.exit(f"加えられません: {e}")
    print(f"加えました: {case['id']}")


if __name__ == "__main__":
    main()
