"""Regression（第18章）: LLM を呼ばない Case を、基準（evaluations/baseline.json）と比べる。数秒で終わり、通常のテストと同じに回す。

経路の規則・承認の関門・入力の検査・承認の Flow は、章をまたいで、壊れやすい（1つの変更が、別の章の振る舞いを変える）。
"""
import pytest

from evaluations.gate import BASELINE_PATH, compare, load_baseline, passed
from evaluations.runner import Harness, evaluate_case, is_deterministic, load_cases

pytestmark = pytest.mark.skipif(not BASELINE_PATH.exists(), reason="baseline.json がありません")


def test_deterministic_cases_meet_the_baseline():
    cases = [c for c in load_cases() if is_deterministic(c)]
    results = [evaluate_case(c, Harness(), runs=1) for c in cases]
    findings = compare(load_baseline(), results)
    assert passed(findings), [f for f in findings if f.level == "FAIL"]
    assert not [f for f in findings if f.level == "WARN"]


def test_baseline_and_cases_are_in_sync():
    """Case を足して基準を更新し忘れる（または、逆）と、ゲートが、その Case を見ない。"""
    base = set(load_baseline()["cases"])
    ids = {c["id"] for c in load_cases()}
    assert base == ids, f"基準にない Case: {sorted(ids - base)} / Case にない基準: {sorted(base - ids)}"
