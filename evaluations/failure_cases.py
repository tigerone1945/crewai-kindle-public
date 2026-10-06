"""失敗の Case 化（第18章）: 本番や開発中に見つけた失敗を、Evaluation Case として、failure_cases.json へ加える。

実行の記録（第17章）は、質問を持たない（BR-10）。そのため、記録から Case を、自動では作れない。
記録が教えてくれるのは、「いつ・どの層で・どの経路で失敗したか」まで。質問は、再現できた文面を、人が書く（利用者の報告、または、同じ種類の質問を作って再現する）。
この Case は、Git にコミットされ、共有される。そのため、質問に、秘密情報・個人情報を含めない（含むものは、加えられない）。
"""
import json
import re
from pathlib import Path

from evaluations.grading import GRADED_EXPECTED_KEYS
from evaluations.runner import EVAL_DIR
from shared.observability import FAILURE_LAYERS
from shared.sensitive import contains_personal, contains_secret

FAILURE_FILE = EVAL_DIR / "failure_cases.json"
MAX_QUESTION_LENGTH = 300
_ID = re.compile(r"^case-f(\d{3})")


class InvalidFailureCase(ValueError):
    pass


def skeleton_from_log(record: dict) -> dict:
    """実行の記録（run_completed の1行）から、Case の下書きを作る。質問は、空（人が書く）。"""
    return {"turns": [""], "expected": {}, "criteria": [], "origin": {"run_id": record.get("run_id"), "route": record.get("route"),
            "failure_layer": record.get("failure_layer") or None, "tool_calls": record.get("tool_calls", [])}}


def next_id(cases: list[dict], slug: str) -> str:
    numbers = [int(m.group(1)) for c in cases if (m := _ID.match(c["id"]))]
    return f"case-f{max(numbers, default=0) + 1:03d}-{slug}"


def validate(turns: list[str], expected: dict, layer: str, notes: str, slug: str) -> None:
    if not turns or not all(isinstance(t, str) and t.strip() for t in turns):
        raise InvalidFailureCase("質問（turns）が空です。再現できた文面を書いてください")
    for t in turns:
        if len(t) > MAX_QUESTION_LENGTH:
            raise InvalidFailureCase(f"質問が長すぎます（{MAX_QUESTION_LENGTH} 字まで）")
        if contains_secret(t) or contains_personal(t):
            raise InvalidFailureCase("質問に、秘密情報または個人情報が含まれています。Case はコミットされます。置き換えてください")
    if not expected:
        raise InvalidFailureCase("expected が空です。何が正しいかを、書いてください")
    unknown = set(expected) - GRADED_EXPECTED_KEYS
    if unknown:
        raise InvalidFailureCase(f"採点されない expected のキーです（綴りの間違いか）: {sorted(unknown)}")
    if layer not in FAILURE_LAYERS:
        raise InvalidFailureCase(f"層は {FAILURE_LAYERS} のいずれかです")
    if not notes.strip():
        raise InvalidFailureCase("notes に、見つかった経緯と、原因の層を書いてください")
    if not re.fullmatch(r"[a-z0-9-]{3,40}", slug):
        raise InvalidFailureCase("slug は、英小文字・数字・ハイフンの 3〜40 字です")


def add_failure_case(turns: list[str], expected: dict, *, layer: str, notes: str, slug: str, title: str,
                     path: Path = FAILURE_FILE, existing_ids: list[str] = ()) -> dict:
    """検査して、failure_cases.json へ加える。id は連番（他のファイルの Case の id とも、重ならない）。"""
    validate(turns, expected, layer, notes, slug)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "version": "1.0", "dataset": "data/ai_agent_practice_sales.csv", "rules_ref": "shared/sales_rules.md", "description": "", "cases": []}
    case = {"id": next_id(data["cases"] + [{"id": i} for i in existing_ids], slug), "title": title, "turns": turns,
            "available_from_chapter": 18, "criteria": sorted({_axis_hint(k) for k in expected}), "expected": expected,
            "notes": f"層: {layer}。{notes}"}
    data["cases"].append(case)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)  # 途中で失敗しても、元のファイルは壊れない
    return case


def _axis_hint(key: str) -> str:
    return {"tools_any_of": "tool_selection", "max_tool_calls": "tool_economy", "must_be_refused": "safety"}.get(key, "completeness")
