"""Crew と Process（第12章）のテスト。LLMは呼ばない。"""
import pytest
from crewai import Process

import shared.analysis_service as service
from shared.roles import DELEGATION_TOOLS, boundary_violations, build_agents, build_tasks


# --- sequential: 順序と担当は、コードで決まっている ---

def test_default_process_is_sequential_and_nobody_can_delegate():
    crew = service.build_role_crew("q")
    assert crew.process == Process.sequential
    assert [t.name for t in crew.tasks] == ["data_quality", "sales_analysis", "business_insight"]  # 実行の順序
    assert all(a.allow_delegation is False for a in crew.agents)  # 委譲は許さない
    assert all(t.agent is not None for t in crew.tasks)  # 担当は Task が決める


def test_sequential_task_tools_follow_the_assigned_role():
    crew = service.build_role_crew("q")
    tools = {t.name: {x.name for x in t.tools} for t in crew.tasks}
    assert tools["data_quality"] == {"check_data_quality"}
    assert tools["business_insight"] == set()


# --- hierarchical: 担当は Manager が決める。権限の境界は、Task が Tool を持たないことで守る ---

def test_hierarchical_tasks_have_no_assignee_and_no_tools():
    crew = service.build_role_crew("q", process="hierarchical")
    assert crew.process == Process.hierarchical
    assert crew.manager_llm is not None
    for task in crew.tasks:
        assert task.agent is None
        assert not task.tools  # Task が Tool を持つと、Manager が直接実行できてしまう（Spike s20）


def test_task_copies_tools_at_construction_so_clearing_agent_later_does_not_remove_them():
    """あとから agent=None にしても Tool は残る。担当を決めない Task は、作るときに agent を渡さない。"""
    agents = build_agents()
    late = build_tasks(agents, "q")[1]
    late.agent = None
    assert late.tools  # 写し取られた Tool が残っている（権限の境界が静かに破れる）
    assert not build_tasks(agents, "q", assign=False)[1].tools


def test_hierarchical_keeps_the_context_chain_and_the_guardrail_on_the_last_task():
    guard = lambda output: (True, output)  # noqa: E731
    crew = service.build_role_crew("q", guardrail=guard, process="hierarchical")
    quality, analysis, insight = crew.tasks
    assert quality in analysis.context and {quality, analysis} <= set(insight.context)
    assert insight.guardrail is not None and quality.guardrail is None


def test_unknown_process_is_rejected():
    with pytest.raises(ValueError, match="未対応"):
        service.build_role_crew("q", process="consensual")


# --- 境界の検査 ---

def test_delegation_tools_are_not_data_access_but_manager_running_data_tools_is_a_violation():
    assert boundary_violations([("Crew Manager", t) for t in DELEGATION_TOOLS]) == []
    problems = boundary_violations([("Crew Manager", "get_sales_summary")])
    assert problems and "Crew Manager" in problems[0]


def test_delegated_work_is_checked_against_the_assigned_role():
    assert boundary_violations([("データ品質担当", "check_data_quality")]) == []
    assert boundary_violations([("データ品質担当", "get_sales_summary")])  # 委譲された Role が、持たない Tool を実行


def test_service_passes_the_process_through(monkeypatch):
    seen = []

    def build(prompt, guardrail=None, guardrail_max_retries=0, process=None):
        seen.append(process)
        raise RuntimeError("stop")

    monkeypatch.setattr(service, "build_role_crew", build)
    for process in (None, "hierarchical"):
        with pytest.raises(RuntimeError):
            service.run_multi_role_analysis("売上の傾向を教えてください。", **({} if process is None else {"process": process}))
    assert seen == ["sequential", "hierarchical"]


def test_delegation_count_counts_only_delegation_tools():
    from shared.roles import delegation_count
    assert delegation_count(["check_data_quality", "delegate_work_to_coworker", "ask_question_to_coworker", "get_sales_summary"]) == 2
    assert delegation_count([]) == 0
