"""Alert（第17章）のテスト: 件数が少ないときは判定しない、層ごとの指標、状態が変わったときだけ知らせる。"""
import json

import pytest

from shared.alerts import (AGENT, BUSINESS, DEFAULT_RULES, RUNTIME, Alert, AlertRule, AlertTracker, Window, evaluate, measure,
                           percentile)
from shared.observability import RunRecord


def run(i=0, **kw) -> RunRecord:
    base = dict(run_id=f"r{i}", ts=1000.0 + i, route="single", outcome="analysis", status="ok", duration_ms=2000, attempts=1,
                prompt_tokens=400, completion_tokens=60)
    return RunRecord(**{**base, **kw})


def failing(i=0, layer="llm"):
    return run(i, outcome="escalated", status="error", error_kind="transient", failure_layer=layer, attempts=3)


def names(runs, requests=(), review_pending=None, rules=DEFAULT_RULES):
    return {a.rule for a in evaluate(Window(runs, requests, review_pending), rules)}


def test_percentile_is_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([7], 95) == 7


def test_alerts_wait_for_enough_samples():
    assert names([failing(i) for i in range(5)]) == set()  # 5件中5件が失敗でも、判定しない
    assert "system_failure_rate_high" in names([failing(i) for i in range(10)])


def test_threshold_is_exclusive():
    runs = [failing(i) for i in range(2)] + [run(i) for i in range(2, 10)]  # 2/10 = 20% ちょうど
    assert "system_failure_rate_high" not in names(runs)
    runs = [failing(i) for i in range(3)] + [run(i) for i in range(3, 10)]  # 30%
    assert "system_failure_rate_high" in names(runs)


def test_input_rejections_and_validation_failures_are_not_system_failures():
    rejected = [run(i, outcome="rejected", status="rejected", failure_layer="input", route="rejected") for i in range(10)]
    assert names(rejected) == {"rejection_rate_high"}  # システムの失敗にならず、業務の層（使い方）として知らせる
    validation = [run(i, outcome="escalated", status="error", error_kind="guardrail", failure_layer="output_validation") for i in range(10)]
    assert names(validation) == {"first_pass_rate_low", "escalation_rate_high"}  # LLM の品質の問題。基盤の失敗ではない


def test_retried_success_counts_against_first_pass_but_not_failure():
    runs = [run(i, attempts=2) for i in range(10)]
    assert names(runs) == {"first_pass_rate_low"}


def test_recovered_tool_errors_are_visible_as_an_agent_quality_signal():
    runs = [run(i, tool_errors=1) for i in range(3)] + [run(i) for i in range(3, 10)]
    fired = evaluate(Window(runs, []), DEFAULT_RULES)
    assert [(a.rule, a.layer) for a in fired] == [("tool_error_runs_high", AGENT)]


def test_latency_uses_p95_of_worked_runs_only():
    runs = [run(i, duration_ms=1000) for i in range(18)] + [run(18, duration_ms=90_000), run(19, duration_ms=95_000)]
    assert names(runs) == {"latency_p95_high"}
    rejected_slow = [run(i, outcome="rejected", failure_layer="input", duration_ms=200_000) for i in range(10)]
    assert "latency_p95_high" not in names(rejected_slow)


def test_cost_alert_uses_mean_tokens_of_answered_runs():
    assert names([run(i, prompt_tokens=7000, completion_tokens=500) for i in range(10)]) == {"tokens_per_run_high"}


def test_busy_rate_counts_only_analysis_requests():
    requests = [(1.0, "/v1/analyses", 429)] * 3 + [(1.0, "/v1/analyses", 200)] * 7 + [(1.0, "/healthz", 200)] * 50
    fired = evaluate(Window([], requests), DEFAULT_RULES)
    assert [(a.rule, a.layer, a.samples) for a in fired] == [("busy_rate_high", RUNTIME, 10)]


def test_review_backlog_alert_and_unknown_count_is_skipped():
    assert names([], review_pending=21) == {"review_backlog_high"}
    assert names([], review_pending=20) == set()
    assert names([], review_pending=None) == set()  # 数えられなかった（DB に届かない）。0件とは扱わない


def test_business_layer_is_named_for_escalations():
    runs = [failing(i) for i in range(10)]
    fired = {a.rule: a.layer for a in evaluate(Window(runs, []), DEFAULT_RULES)}
    assert fired["escalation_rate_high"] == BUSINESS and fired["system_failure_rate_high"] == RUNTIME


def test_measure_reports_every_metric_even_without_samples():
    m = measure(Window([], []))
    assert set(m) >= {"system_failure_rate", "first_pass_rate", "escalation_rate"}
    assert all(v == {"value": None, "samples": 0} for v in m.values())
    assert measure(Window([run(0), run(1, attempts=2)], []))["first_pass_rate"] == {"value": 0.5, "samples": 2}


def test_invalid_rule_is_refused():
    with pytest.raises(ValueError):
        AlertRule("x", RUNTIME, "no_such_metric", ">", 1, 1, "")
    with pytest.raises(ValueError):
        AlertRule("x", RUNTIME, "busy_rate", "~", 1, 1, "")


def alert(rule="system_failure_rate_high"):
    return Alert(rule, RUNTIME, 0.5, 0.2, 10, "d")


def lines(capsys):
    return [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]


def test_tracker_reports_only_transitions(capsys):
    from shared.alerts import configure_alert_logging
    configure_alert_logging()
    t = AlertTracker()
    started, resolved = t.update([alert()], now=1.0)
    assert [a.rule for a in started] == ["system_failure_rate_high"] and resolved == []
    assert t.update([alert()], now=2.0) == ([], [])  # 超えている間は、繰り返さない
    assert t.update([], now=3.0) == ([], ["system_failure_rate_high"])
    assert t.update([], now=4.0) == ([], [])
    out = lines(capsys)
    assert [(l["event"], l["rule"]) for l in out] == [("alert_firing", "system_failure_rate_high"), ("alert_resolved", "system_failure_rate_high")]
    assert out[0]["value"] == 0.5 and out[0]["samples"] == 10
