"""Role（第11章）のテスト。LLMは呼ばない。"""
import datetime

import pytest
from crewai.events import crewai_event_bus
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

import shared.analysis_service as service
from evaluations.checks import check_expected
from shared.roles import (QUALITY_TOOLS, ROLE_NAMES, ROLE_TOOLS, boundary_violations, build_agents, build_tasks,
                          check_data_quality, role_keys)
from shared.sales_db import SalesRepository
from shared.sales_tools import SALES_TOOLS
from shared.schemas import Metric, SalesAnalysisResult
from shared.tracing import trace_run


@pytest.fixture(scope="module")
def agents():
    return build_agents()


# --- Role の定義 ---

def test_each_role_has_only_its_own_tools(agents):
    tools = {k: {t.name for t in a.tools} for k, a in agents.items()}
    assert tools["data_quality"] == {"check_data_quality"}
    assert tools["sales_analyst"] == {t.name for t in SALES_TOOLS}
    assert tools["business_insight"] == set()  # 示唆担当は、データを直接読めない（Context Boundary）
    assert {k: sorted(v) for k, v in ROLE_TOOLS.items()} == {k: sorted(t) for k, t in tools.items()}


def test_tool_sets_do_not_overlap(agents):
    names = [t.name for a in agents.values() for t in a.tools]
    assert len(names) == len(set(names))


def test_no_role_can_delegate_or_execute_arbitrary_code(agents):
    for a in agents.values():
        assert a.allow_delegation is False
        assert not any(w in t.name for t in a.tools for w in ("sql", "python", "exec", "eval", "review", "create"))  # BR-04, BR-05


def test_roles_have_distinct_names(agents):
    assert sorted(a.role for a in agents.values()) == sorted(ROLE_NAMES.values())


# --- Task の責務と Context ---

def test_tasks_are_one_per_role_with_named_traceable_names(agents):
    tasks = build_tasks(agents, "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
    assert [t.name for t in tasks] == ["data_quality", "sales_analysis", "business_insight"]
    assert [t.agent.role for t in tasks] == [ROLE_NAMES[k] for k in ("data_quality", "sales_analyst", "business_insight")]
    assert not any("売上傾向" in t.name for t in tasks)  # 質問文は Task の名前（Trace に残る）に入らない


def test_context_flows_forward_only(agents):
    quality, analysis, insight = build_tasks(agents, "q")
    assert not isinstance(quality.context, list)  # 最初の Task は、前工程を持たない（未指定）
    assert [t.name for t in analysis.context] == ["data_quality"]
    assert [t.name for t in insight.context] == ["data_quality", "sales_analysis"]


def test_only_the_final_task_has_the_schema_and_the_guardrail(agents):
    guard = lambda o: (True, o)  # noqa: E731
    quality, analysis, insight = build_tasks(agents, "q", guardrail=guard, guardrail_max_retries=0)
    assert insight.output_pydantic is SalesAnalysisResult and insight.guardrail is not None
    assert quality.output_pydantic is None and analysis.output_pydantic is None
    assert quality.guardrail is None and analysis.guardrail is None


def test_the_insight_task_forbids_new_numbers_in_its_instructions(agents):
    insight = build_tasks(agents, "q")[-1]
    assert "新たに計算" in insight.description and "integrated_analysis" in insight.description


# --- データ品質 ---

def test_data_quality_facts_from_the_database():
    q = SalesRepository.default().data_quality()
    assert q["order_count"] == 300 and q["cancelled_count"] == 14 and q["review_count"] == 37
    assert (q["period_start"], q["period_end"]) == ("2026-01-01", "2026-09-16")
    assert (q["sales_exceeds_total_count"], q["cancelled_with_sales_count"], q["orders_without_customer_or_product"]) == (0, 0, 0)
    assert q["cancelled_share_pct"] == 4.7 and q["review_share_pct"] == 12.3


def test_data_quality_tool_output_is_readable_and_has_the_facts():
    out = check_data_quality.run()
    assert "総レコード数: 300" in out and "キャンセル件数: 14（4.7%）" in out and "要レビュー件数: 37（12.3%）" in out
    assert out.count("\n") >= 5 and "\\n" not in out  # 改行が文字として残っていない


def test_data_quality_tool_is_read_only_and_listed_for_its_role_only():
    assert [t.name for t in QUALITY_TOOLS] == ["check_data_quality"]
    assert "check_data_quality" not in {t.name for t in SALES_TOOLS}


def test_quality_numbers_are_legitimate_values_for_the_output_guard():
    from shared.sales_validation import find_ungrounded_metrics

    r = SalesAnalysisResult(question="q", analysis_type="integrated_analysis", summary="s", insights=["a", "b"], recommendations=["c"],
                            metrics=[Metric(label="キャンセル率", value=4.7, unit="%"), Metric(label="要レビュー", value=37, unit="件"),
                                     Metric(label="売上合計", value=28298695, unit="円")])
    assert find_ungrounded_metrics(r, SalesRepository.default()) == []
    bad = r.model_copy(update={"metrics": [Metric(label="キャンセル率", value=5.1, unit="%")]})
    assert find_ungrounded_metrics(bad, SalesRepository.default())


# --- 権限の境界・Role の記録 ---

def test_boundary_is_respected_when_each_role_uses_only_its_own_tools():
    assert boundary_violations([(ROLE_NAMES["data_quality"], "check_data_quality"),
                                (ROLE_NAMES["sales_analyst"], "get_sales_by_region")]) == []


@pytest.mark.parametrize("role,tool", [("business_insight", "get_sales_summary"), ("data_quality", "get_sales_by_region"),
                                       ("sales_analyst", "check_data_quality")])
def test_a_tool_run_by_the_wrong_role_is_reported(role, tool):
    problems = boundary_violations([(ROLE_NAMES[role], tool)])
    assert problems and tool in problems[0]


def test_unknown_role_names_are_reported_as_violations_and_ignored_as_roles():
    assert boundary_violations([("謎の担当", "get_sales_summary")])
    assert role_keys(["データ品質担当", "謎の担当", "業務示唆担当"]) == ["data_quality", "business_insight"]


def test_trace_records_which_role_ran_which_tool():
    now = datetime.datetime.now()
    with trace_run() as trace:
        crewai_event_bus.emit(None, ToolUsageFinishedEvent(
            tool_name="check_data_quality", tool_args={}, tool_class="T", started_at=now,
            finished_at=now + datetime.timedelta(milliseconds=5), output="x", from_cache=False,
            agent_role=ROLE_NAMES["data_quality"]))
    assert trace.summary()["tool_roles"] == [(ROLE_NAMES["data_quality"], "check_data_quality")]


# --- Case 07 の判定 ---

CASE_07 = {"roles_invoked": ["data_quality", "sales_analyst", "business_insight"], "must_contain_numbers_any_of": [28298695]}


def test_case_07_passes_when_all_roles_ran_and_the_total_is_present():
    assert check_expected(CASE_07, "売上合計は28,298,695円です", [], roles_invoked=["data_quality", "sales_analyst", "business_insight"]) == []


def test_case_07_fails_when_a_role_did_not_run():
    failures = check_expected(CASE_07, "売上合計は28,298,695円です", [], roles_invoked=["sales_analyst", "business_insight"])
    assert any("data_quality" in f for f in failures)


def test_case_07_fails_without_any_expected_number_or_role_info():
    assert any("数値" in f for f in check_expected(CASE_07, "売上は好調です", [], roles_invoked=list(CASE_07["roles_invoked"])))
    assert any("渡されていません" in f for f in check_expected(CASE_07, "28,298,695円", []))


# --- サービス ---

def test_multi_role_service_applies_the_input_guardrail_before_building_any_crew(monkeypatch):
    monkeypatch.setattr(service, "build_role_crew", lambda *a, **k: pytest.fail("Crew を作ってはいけない"))
    with pytest.raises(service.InputRejected):
        service.run_multi_role_analysis("Pythonでシューティングゲームを作ってください。")


def test_multi_role_service_shares_the_guarded_retry_loop(monkeypatch):
    built = []

    class Crew:
        def __init__(self, prompt, guard):
            self.prompt, self.guard = prompt, guard

        def kickoff(self):
            raise Exception("Task failed guardrail validation after 0 retries. Last error: x")

    def build(prompt, guardrail=None, guardrail_max_retries=0):
        built.append(prompt)
        guard_obj = guardrail.__self__
        guard_obj.rejections, guard_obj.last_reason = 1, "60%は実データにありません"
        return Crew(prompt, guard_obj)

    monkeypatch.setattr(service, "build_role_crew", build)
    with pytest.raises(service.AnalysisError):
        service.run_multi_role_analysis("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
    assert len(built) == 3 and "却下" in built[1] and "60%" in built[2]
