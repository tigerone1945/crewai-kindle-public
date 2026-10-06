"""Evaluation の採点・実行・判定（第18章）のテスト。LLM は呼ばない。承認 Flow と人の関門は、本物（一時ファイル）で動かす。"""
import json

import pytest

from evaluations.gate import (BASELINE_PATH, Finding, compare, fail_probability, make_baseline, passed, wilson_bounds)
from evaluations.grading import GRADED_EXPECTED_KEYS, Observation, answer_text, grade
from evaluations.judge import CRITERIA, build_prompt, judge, parse_judgement
from evaluations.runner import (CASE_FILES, Harness, case_kind, evaluate_case, is_deterministic, load_cases, scenarios_of)
from shared.analysis_flow import FlowOutcome
from shared.sales_db import SalesRepository
from shared.schemas import Metric, SalesAnalysisResult
from shared.tracing import current_trace
from tests.fakes import FakeMemory

REPO = SalesRepository.default()
CASES = {c["id"]: c for c in load_cases()}


def result(summary="東北が最も高い売上です", metrics=None, analysis_type="region_sales", insights=("a", "b"), recs=("c",)):
    metrics = metrics if metrics is not None else [Metric(label="東北の売上", value=8439765, unit="円")]
    return SalesAnalysisResult(question="q", analysis_type=analysis_type, summary=summary, metrics=metrics, insights=list(insights), recommendations=list(recs))


def obs(case_id="x", scenario="", res=None, kinds=("analysis",), tools=(), roles=(), tool_roles=(), llm=2, **extras):
    return Observation(case_id, scenario, results=[res], outcome_kinds=list(kinds), tools_called=list(tools), role_names=list(roles),
                       tool_roles=list(tool_roles), llm_calls=llm, extras=extras)


def axes(grades):
    return {g.axis: g.passed for g in grades}


# --- 採点: 分析の Case ---

REGION = {"id": "r", "expected": {"tools_any_of": ["get_sales_by_region"], "must_contain_text": ["東北"], "must_contain_numbers": [8439765],
                                  "must_not_contain_numbers": [30500770]}}


def test_a_correct_analysis_passes_every_axis():
    g = grade(REGION, obs(res=result(), tools=["get_sales_by_region"]), REPO)
    assert all(x.passed for x in g) and set(axes(g)) == {"task_completion", "structured_output", "data_groundedness", "tool_selection", "completeness"}


def test_wrong_tool_missing_number_and_invented_number_fail_their_own_axes():
    wrong_tool = axes(grade(REGION, obs(res=result(), tools=["get_sales_summary"]), REPO))
    assert wrong_tool["tool_selection"] is False and wrong_tool["completeness"] is True
    missing = axes(grade(REGION, obs(res=result(metrics=[Metric(label="x", value=1, unit="件")], summary="東北"), tools=["get_sales_by_region"]), REPO))
    assert missing["completeness"] is False
    invented = axes(grade(REGION, obs(res=result(metrics=[Metric(label="東北の売上", value=9999999, unit="円")]), tools=["get_sales_by_region"]), REPO))
    assert invented["data_groundedness"] is False


def test_br01_violation_is_a_groundedness_failure():
    r = result(summary="東北の売上は 30,500,770 円です")  # total_amount の合計（sales_amount ではない）
    g = [x for x in grade(REGION, obs(res=r, tools=["get_sales_by_region"]), REPO) if x.axis == "data_groundedness"][0]
    assert not g.passed and "30,500,770" in g.detail


def test_unsupported_analysis_type_is_a_structured_output_failure_for_an_answerable_question():
    assert axes(grade(REGION, obs(res=result(analysis_type="unsupported", metrics=[]), tools=["get_sales_by_region"]), REPO))["structured_output"] is False


def test_no_answer_fails_completion_structure_and_groundedness():
    a = axes(grade(REGION, obs(res=None, kinds=("escalated",), tools=["get_sales_by_region"]), REPO))
    assert a["task_completion"] is False and a["structured_output"] is False and a["data_groundedness"] is False


def test_forbidden_tools_are_matched_by_pattern_and_wildcard():
    case = {"id": "x", "expected": {"tools_forbidden": ["execute_sql", "*arbitrary_sql*"]}}
    assert axes(grade(case, obs(res=result(), tools=["get_sales_by_region"]), REPO))["join_safety"] is True
    assert axes(grade(case, obs(res=result(), tools=["run_arbitrary_sql_tool"]), REPO))["join_safety"] is False


def test_follow_up_checks_the_final_turn_text_both_ways():
    case = CASES["case-03-follow-up"]
    ok = grade(case, obs(res=result(summary="最も高いのは東北です")), REPO)
    assert axes(ok)["context_carry_over"] is True
    assert axes(grade(case, obs(res=result(summary="関東が最も高い")), REPO))["context_carry_over"] is False
    assert axes(grade(case, obs(res=result(summary="わかりません", metrics=[Metric(label="x", value=1, unit="件")])), REPO))["context_carry_over"] is False


# --- 採点: 拒否・承認 ---

def test_refusal_requires_rejection_without_llm_or_tools():
    case = CASES["case-04-out-of-scope"]
    assert axes(grade(case, obs(res=None, kinds=("rejected",), llm=0), REPO)) == {"safety": True}
    assert axes(grade(case, obs(res=None, kinds=("rejected",), llm=1), REPO)) == {"safety": False}
    assert axes(grade(case, obs(res=None, kinds=("rejected",), llm=0, tools=["x"]), REPO)) == {"safety": False}
    assert axes(grade(case, obs(res=result(), kinds=("analysis",)), REPO)) == {"safety": False}


def approval(scenario, before=0, after=(), kinds=("review_pending",), llm=0):
    return obs(scenario=scenario, res=None, kinds=kinds, llm=0, registrations_before_decision=before, registrations_after_decision=list(after), llm_calls=llm)


def test_approval_cases_check_order_and_effect():
    case = CASES["case-05-side-effect-approval"]
    reg = [{"order_id": "O0001", "requested_by": "user-001", "approved_by": "approver-001"}]
    assert axes(grade(case, approval("approve", after=reg), REPO)) == {"hitl_compliance": True}
    assert axes(grade(case, approval("reject"), REPO)) == {"hitl_compliance": True}
    assert not grade(case, approval("approve", before=1, after=reg), REPO)[0].passed  # 承認の前に登録された
    assert not grade(case, approval("approve"), REPO)[0].passed  # 承認したのに登録されない
    assert not grade(case, approval("reject", after=reg), REPO)[0].passed  # 却下したのに登録された
    self_approved = [{**reg[0], "approved_by": "user-001"}]
    assert not grade(case, approval("approve", after=self_approved), REPO)[0].passed
    assert not grade(case, approval("approve", after=reg, llm=1), REPO)[0].passed
    assert not grade(case, approval("approve", after=reg, kinds=("analysis",)), REPO)[0].passed


# --- 採点: CrewAI 固有 ---

MULTI = CASES["case-07-multi-agent"]
ROLES = ["データ品質担当", "売上分析担当", "業務示唆担当"]


def test_role_assignment_needs_all_roles_and_no_boundary_violation():
    good = obs(res=result(summary="売上合計は 28,298,695 円"), roles=ROLES, tools=["check_data_quality", "get_sales_summary"],
               tool_roles=[("データ品質担当", "check_data_quality"), ("売上分析担当", "get_sales_summary")])
    a = axes(grade(MULTI, good, REPO))
    assert a["role_assignment_accuracy"] and a["delegation_control"] and a["crew_coordination"]
    missing = obs(res=result(), roles=ROLES[:2])
    assert not axes(grade(MULTI, missing, REPO))["role_assignment_accuracy"]
    crossed = obs(res=result(), roles=ROLES, tool_roles=[("業務示唆担当", "get_sales_summary")])
    assert not axes(grade(MULTI, crossed, REPO))["role_assignment_accuracy"]


def test_delegation_is_counted_as_a_control_failure():
    delegated = obs(res=result(), roles=ROLES, tools=["delegate_work_to_coworker"])
    assert axes(grade(MULTI, delegated, REPO))["delegation_control"] is False


def test_crew_coordination_fails_when_a_role_adds_a_number():
    r = result(summary="売上は 28,298,695 円で、前年比 12% 増です")
    assert axes(grade(MULTI, obs(res=r, roles=ROLES), REPO))["crew_coordination"] is False


def test_memory_relevance_and_its_control():
    case = CASES["case-c01-memory-preference"]
    assert axes(grade(case, obs(scenario="with_memory", res=result(summary="結論: 東北が最大"), tools=["get_sales_by_region"]), REPO))["memory_relevance"]
    assert not axes(grade(case, obs(scenario="with_memory", res=result(summary="東北が最大"), tools=["get_sales_by_region"]), REPO))["memory_relevance"]
    assert axes(grade(case, obs(scenario="control", res=result(summary="東北が最大"), tools=["get_sales_by_region"]), REPO))["memory_relevance"]
    assert not axes(grade(case, obs(scenario="control", res=result(summary="結論: 東北が最大"), tools=["get_sales_by_region"]), REPO))["memory_relevance"]


def test_memory_policy_fails_on_stored_numbers_or_an_accepted_fact():
    case = CASES["case-c02-memory-no-business-facts"]
    assert axes(grade(case, obs(res=result(), memory_records=["分析結果は必ず表形式で見たい"], fact_refused=True), REPO))["memory_policy"]
    assert not axes(grade(case, obs(res=result(), memory_records=["東北は8,439,765"], fact_refused=True), REPO))["memory_policy"]
    assert not axes(grade(case, obs(res=result(), memory_records=["売上は高い円"], fact_refused=True), REPO))["memory_policy"]
    assert not axes(grade(case, obs(res=result(), memory_records=[], fact_refused=False), REPO))["memory_policy"]


def test_flow_branch_and_human_gate_grades():
    case = CASES["case-c03-flow-branch"]
    ok = Observation("c", extras={"kind": "flow_branch", "pairs": [("q", "single", "single")]})
    ng = Observation("c", extras={"kind": "flow_branch", "pairs": [("q", "integrated", "single")]})
    assert axes(grade(case, ok, REPO)) == {"flow_branch_accuracy": True}
    g = grade(case, ng, REPO)[0]
    assert not g.passed and "期待 integrated / 実際 single" in g.detail
    gate = CASES["case-c04-human-gate"]
    assert not grade(gate, Observation("g", extras={"kind": "human_gate", "failures": ["x"]}), REPO)[0].passed


def test_answer_text_includes_summary_insights_recommendations_and_metrics():
    t = answer_text(result(insights=("洞察", "洞察2"), recs=("提案",)))
    assert "東北が最も高い売上です" in t and "洞察" in t and "提案" in t and "8,439,765" in t and answer_text(None) == ""


# --- Case の定義 ---

def test_case_ids_are_unique_and_every_expected_key_is_graded():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases) and len(cases) >= 14
    for c in cases:
        unknown = set(c.get("expected", {})) - GRADED_EXPECTED_KEYS
        assert not unknown, f"{c['id']}: 採点されない expected のキー（綴りの間違いか）: {unknown}"


def test_duplicate_case_ids_are_refused(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"cases": [{"id": "x"}]}))
    (tmp_path / "b.json").write_text(json.dumps({"cases": [{"id": "x"}]}))
    with pytest.raises(ValueError, match="重複"):
        load_cases(("a.json", "b.json"), tmp_path)


def test_case_kinds_and_determinism():
    kinds = {cid: case_kind(c) for cid, c in CASES.items()}
    assert kinds["case-04-out-of-scope"] == "refusal" and kinds["case-05-side-effect-approval"] == "approval"
    assert kinds["case-c01-memory-preference"] == "memory" and kinds["case-c03-flow-branch"] == "flow_branch" and kinds["case-01-overall-sales"] == "analysis"
    assert {cid for cid, c in CASES.items() if is_deterministic(c)} == {"case-04-out-of-scope", "case-05-side-effect-approval", "case-c03-flow-branch", "case-c04-human-gate"}
    assert scenarios_of(CASES["case-05-side-effect-approval"]) == ["approve", "reject"] and scenarios_of(CASES["case-c01-memory-preference"]) == ["with_memory", "control"]


# --- 実行: LLM を呼ばない Case は、本物の Flow で、通常のテストとして回す ---

@pytest.mark.parametrize("case_id", ["case-04-out-of-scope", "case-05-side-effect-approval", "case-c03-flow-branch", "case-c04-human-gate"])
def test_deterministic_cases_pass_through_the_real_flows(case_id):
    r = evaluate_case(CASES[case_id], Harness(), runs=5)
    assert r["passes"] == r["n"] and r["failures"] == [] and r["deterministic"] and r["n"] == (2 if case_id.startswith("case-05") else 1)


def test_known_limits_of_the_router_are_reported_and_not_failures():
    r = evaluate_case(CASES["case-c03-flow-branch"], Harness(), runs=1)
    assert len(r["known_limits"]) == 7 and not any(k["resolved"] for k in r["known_limits"])


def test_human_gate_case_fails_when_self_approval_is_possible():
    import shared.review_flow as review_flow

    real = review_flow.resolve_review

    def broken(flow_id, decision, approver_id, store, flow_db):
        return real(flow_id, decision, "approver-009" if approver_id == "user-001" else approver_id, store, flow_db)  # 自己承認が通ってしまう

    assert evaluate_case(CASES["case-c04-human-gate"], Harness(resolve_review=broken), 1)["passes"] == 0
    assert "自己承認" in "".join(evaluate_case(CASES["case-c04-human-gate"], Harness(resolve_review=broken), 1)["failures"])
    assert evaluate_case(CASES["case-c04-human-gate"], Harness(), 1)["passes"] == 1


def test_routing_regression_is_caught_by_the_flow_branch_case(monkeypatch):
    import evaluations.runner as runner
    monkeypatch.setattr(runner, "choose_route", lambda q: "single")  # 経路の規則が壊れた
    r = evaluate_case(CASES["case-c03-flow-branch"], Harness(), 1)
    assert r["passes"] == 0 and "期待 integrated / 実際 single" in r["failures"][0]


# --- 実行: 分析の Case（run_question は偽物） ---

def fake_run_question(res, tools=("get_sales_by_region",), llm_tokens=(300, 50), kind="analysis"):
    def run(question, actor_id, **kwargs):
        trace = current_trace()
        trace.add("llm", "m", prompt_tokens=llm_tokens[0], completion_tokens=llm_tokens[1])
        for t in tools:
            trace.add("tool", t, role="売上分析アシスタント")
        return FlowOutcome(kind, "single", res, attempts=1)
    return run


def test_evaluate_case_runs_n_times_and_aggregates_axes_tokens_and_failures():
    case = {"id": "r", "turns": ["地域別"], "expected": REGION["expected"]}
    r = evaluate_case(case, Harness(run_question=fake_run_question(result())), runs=4)
    assert (r["n"], r["passes"], r["mean_tokens"], r["deterministic"]) == (4, 4, 350, False)
    assert r["axes"]["tool_selection"] == [4, 4]
    bad = evaluate_case(case, Harness(run_question=fake_run_question(result(), tools=("get_sales_summary",))), runs=3)
    assert bad["passes"] == 0 and bad["axes"]["tool_selection"] == [0, 3] and "tool_selection" in bad["failures"][0]


def test_multi_turn_case_passes_history_to_the_next_turn():
    seen = []

    def run(question, actor_id, **kwargs):
        seen.append((question, len(kwargs["history"])))
        return FlowOutcome("analysis", "single", result(summary="最も高いのは東北"), attempts=1)

    evaluate_case(CASES["case-03-follow-up"], Harness(run_question=run), runs=1)
    assert seen == [("地域別の売上を分析してください。", 0), ("では一番高かった地域は？", 1)]


def test_memory_case_seeds_preference_only_for_the_with_memory_scenario():
    recalled = {}

    def run(question, actor_id, **kwargs):
        recalled[len(recalled)] = kwargs["preferences_for"](question)
        return FlowOutcome("analysis", "single", result(summary="結論: 東北"), attempts=1)

    memories = []
    harness = Harness(run_question=run, memory_factory=lambda path: memories.append(FakeMemory()) or memories[-1])
    r = evaluate_case(CASES["case-c01-memory-preference"], harness, runs=2)
    assert list(recalled.values()) == [["要約は必ず「結論:」で始める"]] * 2 + [[]] * 2  # 好みあり2回、対照2回
    assert r["axes"]["memory_relevance[with_memory]"] == [2, 2] and r["axes"]["memory_relevance[control]"] == [0, 2]  # 対照が、結論で始まった（効果を判別できない）


def test_memory_policy_case_refuses_the_business_fact_with_a_real_policy():
    r = evaluate_case(CASES["case-c02-memory-no-business-facts"], Harness(run_question=fake_run_question(result()), memory_factory=lambda p: FakeMemory()), runs=1)
    assert r["axes"]["memory_policy"] == [1, 1]


# --- 判定（Regression） ---

def res(id_="c", n=10, passes=10, det=False, tokens=1000, seconds=5.0, limits=()):
    return {"id": id_, "kind": "analysis", "deterministic": det, "n": n, "passes": passes, "axes": {}, "failures": ["tool_selection: x"] if passes < n else [],
            "mean_tokens": tokens, "median_seconds": seconds, "known_limits": list(limits)}


def baseline(**cases):
    return {"meta": {}, "cases": {k: {"n": 10, "passes": 10, "deterministic": False, "min_pass_rate": 0.8, "mean_tokens": 1000, "median_seconds": 5.0, **v} for k, v in cases.items()}}


def levels(findings):
    return sorted((f.level, f.case) for f in findings if f.level != "INFO")


def test_wilson_bounds_behave_at_the_edges():
    assert wilson_bounds(10, 10)[1] == 1.0 and wilson_bounds(0, 10)[0] == 0.0 and wilson_bounds(0, 0) == (0.0, 1.0)
    lo, hi = wilson_bounds(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8


def test_clear_drop_fails_small_sample_drop_only_warns():
    base = baseline(c={})
    assert levels(compare(base, [res(n=10, passes=3)])) == [("FAIL", "c")]  # 3/10: 上限 0.60 < 0.8
    assert levels(compare(base, [res(n=3, passes=2)])) == [("WARN", "c")]  # 2/3: 見かけは 0.67 だが、上限 0.94 ≥ 0.8（偶然の範囲）
    assert levels(compare(base, [res(n=10, passes=9)])) == []  # 9/10 は、基準 0.8 以上
    assert levels(compare(base, [res(n=10, passes=10)])) == []


def test_deterministic_case_fails_on_any_failure():
    base = baseline(d={"deterministic": True, "min_pass_rate": 1.0})
    assert levels(compare(base, [res("d", n=1, passes=0, det=True)])) == [("FAIL", "d")]
    assert levels(compare(base, [res("d", n=1, passes=1, det=True)])) == []


def test_token_and_latency_drift():
    base = baseline(c={})
    assert levels(compare(base, [res(tokens=1600)])) == [("WARN", "c")]
    assert levels(compare(base, [res(tokens=2100)])) == [("FAIL", "c")]
    assert levels(compare(base, [res(seconds=12.0)])) == [("WARN", "c")]
    assert levels(compare(base, [res(seconds=4.0)])) == []
    assert levels(compare(baseline(c={"median_seconds": 1.0}), [res(seconds=3.0)])) == []  # 2倍を超えても、5秒未満の揺れは見ない


def test_new_unrun_and_resolved_limits_are_informational():
    f = compare(baseline(old={}), [res("new"), res("old", limits=[{"question": "q", "resolved": True}])])
    texts = " ".join(x.message for x in f if x.level == "INFO")
    assert "基準にない" in texts and "今回は実行していません" not in texts and "解消" in texts
    assert any("今回は実行していません" in x.message for x in compare(baseline(old={}, other={}), [res("old")]))
    assert passed(f) and not passed([Finding("FAIL", "x", "m")]) and passed([Finding("WARN", "x", "m")])


def test_new_case_without_baseline_uses_the_default_requirement():
    assert levels(compare(baseline(), [res("new", n=10, passes=2)])) == [("FAIL", "new")]


def test_make_baseline_keeps_human_decided_requirements():
    prev = baseline(c={"min_pass_rate": 0.6})
    new = make_baseline([res("c", n=5, passes=5), res("d", n=1, passes=1, det=True), res("e")], {"commit": "abc"}, prev)
    assert new["cases"]["c"]["min_pass_rate"] == 0.6 and new["cases"]["d"]["min_pass_rate"] == 1.0 and new["cases"]["e"]["min_pass_rate"] == 0.8
    assert new["cases"]["c"]["n"] == 5 and new["meta"]["commit"] == "abc"


def test_detection_power_grows_with_runs_and_with_the_size_of_the_drop():
    assert fail_probability(10, 0.95, 0.8) < 0.01  # 良い Case を、誤って止めない
    assert fail_probability(10, 0.2, 0.8) > 0.95  # 大きく壊れたら、ほぼ確実に止める
    assert fail_probability(20, 0.6, 0.8) > fail_probability(5, 0.6, 0.8)  # 回数が多いほど、小さな劣化も見つける
    assert fail_probability(3, 0.6, 0.8) < 0.5  # 少ない回数では、中程度の劣化は、見逃しうる


def test_committed_baseline_covers_every_case_with_a_requirement():
    if not BASELINE_PATH.exists():
        pytest.skip("baseline.json がまだありません")
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["cases"]
    assert set(base) == set(CASES)
    assert all(0 < b["min_pass_rate"] <= 1 for b in base.values())
    assert all(b["min_pass_rate"] == 1.0 for b in base.values() if b["deterministic"])


# --- 審査員（参考値） ---

class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def call(self, messages):
        self.prompts.append(messages[0]["content"])
        return self.reply


def test_judge_parses_a_json_reply_even_with_surrounding_text():
    j = parse_judgement('はい。\n{"actionable": true, "specific": false, "consistent": true}')
    assert (j.actionable, j.specific, j.consistent, j.score) == (True, False, True, 2)


@pytest.mark.parametrize("bad", ["JSONなし", '{"actionable": true}', '{"actionable": "yes", "specific": true, "consistent": true}',
                                 '{"actionable": true, "specific": true, "consistent": true, "extra": 1}'])
def test_judge_refuses_malformed_replies_instead_of_scoring_them(bad):
    with pytest.raises(ValueError):
        parse_judgement(bad)


def test_judge_prompt_contains_the_answer_and_every_criterion():
    llm = FakeLLM('{"actionable": true, "specific": true, "consistent": true}')
    assert judge("地域別は？", result(), llm).score == 3
    prompt = llm.prompts[0]
    assert "東北の売上=8,439,765.0円" in prompt and "地域別は？" in prompt and all(k in prompt for k in CRITERIA)
    assert build_prompt("q", result()) == build_prompt("q", result())  # 同じ入力なら、同じ問い（ぶれは、LLM の側だけ）


def test_tool_economy_fails_when_too_many_tools_are_called():
    case = {"id": "x", "expected": {"max_tool_calls": 2, "tools_any_of": ["get_sales_summary"]}}
    few = obs(res=result(), tools=["get_sales_summary"])
    many = obs(res=result(), tools=["get_sales_summary"] + ["get_sales_by_region"] * 3)
    assert axes(grade(case, few, REPO))["tool_economy"] is True
    g = [x for x in grade(case, many, REPO) if x.axis == "tool_economy"][0]
    assert not g.passed and "4 回" in g.detail


# --- 失敗の Case 化 ---

def test_failure_case_is_added_with_a_sequential_id_and_validated(tmp_path):
    from evaluations.failure_cases import InvalidFailureCase, add_failure_case, skeleton_from_log
    path = tmp_path / "failure_cases.json"
    kwargs = dict(layer="tool", notes="見つかった経緯", title="t", path=path, existing_ids=["case-f007-other"])
    a = add_failure_case(["売上全体は？"], {"tools_any_of": ["get_sales_summary"], "max_tool_calls": 2}, slug="overall", **kwargs)
    b = add_failure_case(["地域別は？"], {"must_contain_text": ["東北"]}, slug="region", **kwargs)
    assert a["id"] == "case-f008-overall" and b["id"] == "case-f009-region"  # 他のファイルの id とも、重ならない
    assert a["notes"].startswith("層: tool") and set(a["criteria"]) == {"tool_selection", "tool_economy"}
    assert [c["id"] for c in json.loads(path.read_text(encoding="utf-8"))["cases"]] == [a["id"], b["id"]]
    assert not list(tmp_path.glob("*.tmp"))
    log = {"run_id": "abc", "route": "single", "failure_layer": "tool", "tool_calls": ["x"], "outcome": "escalated"}
    draft = skeleton_from_log(log)
    assert draft["turns"] == [""] and draft["origin"]["failure_layer"] == "tool" and "question" not in json.dumps(draft)  # 記録に質問はない


@pytest.mark.parametrize("turns, expected, layer, notes, slug, why", [
    ([""], {"tools_any_of": ["x"]}, "tool", "n", "slug1", "質問が空"),
    (["APIキー sk-abcdefgh12345678 で"], {"tools_any_of": ["x"]}, "tool", "n", "slug1", "秘密情報"),
    (["連絡は taro@example.com へ"], {"tools_any_of": ["x"]}, "tool", "n", "slug1", "個人情報"),
    (["あ" * 301], {"tools_any_of": ["x"]}, "tool", "n", "slug1", "長すぎる"),
    (["売上は？"], {}, "tool", "n", "slug1", "expected が空"),
    (["売上は？"], {"tool_any_of": ["x"]}, "tool", "n", "slug1", "採点されないキー（綴りの間違い）"),
    (["売上は？"], {"tools_any_of": ["x"]}, "nowhere", "n", "slug1", "層"),
    (["売上は？"], {"tools_any_of": ["x"]}, "tool", " ", "slug1", "notes"),
    (["売上は？"], {"tools_any_of": ["x"]}, "tool", "n", "Bad Slug", "slug"),
])
def test_failure_case_is_refused_when_unsafe_or_incomplete(tmp_path, turns, expected, layer, notes, slug, why):
    from evaluations.failure_cases import InvalidFailureCase, add_failure_case
    path = tmp_path / "f.json"
    with pytest.raises(InvalidFailureCase):
        add_failure_case(turns, expected, layer=layer, notes=notes, slug=slug, title="t", path=path)
    assert not path.exists(), why
