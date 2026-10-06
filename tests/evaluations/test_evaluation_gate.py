"""Evaluation（第18章）: すべての Case を、本物の Flow・Crew・LLM で実行し、基準と比べる。デプロイの前に回す（APIキーが必要。数分かかる）。

LLM の出力は確率的。FAIL（基準を下回ると、統計的に言える）だけが、テストの失敗。WARN（回数が少なく、偶然の範囲）は、失敗にしない。
回数は、環境変数 EVAL_RUNS（既定 3）。回数を増やすほど、小さな劣化を見つけられる（evaluations/gate.py）。
"""
import os

import pytest

from evaluations.gate import BASELINE_PATH, compare, load_baseline, passed
from evaluations.runner import Harness, evaluate_case, load_cases
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    (not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists()) or not BASELINE_PATH.exists(),
    reason="LLMを呼ぶためAPIキーが必要（と、baseline.json）",
)


def test_all_cases_meet_the_baseline():
    runs = int(os.getenv("EVAL_RUNS", "3"))
    harness = Harness()
    results = [evaluate_case(c, harness, runs) for c in load_cases()]
    findings = compare(load_baseline(), results)
    assert passed(findings), "\n".join(f"{f.level} {f.case}: {f.message}" for f in findings if f.level != "INFO")
