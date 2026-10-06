"""運用の観測（第17章）のテスト: 失敗の層の判定、実行の記録、Metric、直近の窓。LLM は呼ばない。"""
import dataclasses
import json
import re

from shared.analysis_flow import FlowOutcome
from shared.observability import (OTHER, Observer, RunRecord, build_run_record, classify_failure, configure_run_logging)
from shared.tracing import RunTrace


def trace_with(*events):
    """(kind, name, status, at_ms, attrs) から、Trace を作る。"""
    trace = RunTrace("run000000001")
    for kind, name, status, at_ms, *rest in events:
        trace.add(kind, name, status, at=trace.started_at + at_ms / 1000, **(rest[0] if rest else {}))
    return trace


def record(**kw) -> RunRecord:
    base = dict(run_id="r1", ts=1000.0, route="single", outcome="analysis", status="ok", duration_ms=2000, attempts=1)
    return RunRecord(**{**base, **kw})


# --- 失敗の層 ---

def test_success_and_pending_review_have_no_failure_layer():
    assert classify_failure("analysis", "", []) == ""
    assert classify_failure("review_pending", "", []) == ""


def test_rejected_input_is_the_input_layer_and_not_a_system_failure():
    assert classify_failure("rejected", "", []) == "input"
    assert classify_failure("review_invalid", "", []) == "input"
    assert record(outcome="rejected", failure_layer="input").failed is False


def test_guardrail_exhaustion_is_output_validation_even_if_trace_has_errors():
    trace = trace_with(("tool", "get_sales_by_region", "error", 100, {"error": "x"}))
    assert classify_failure("escalated", "guardrail", trace.events) == "output_validation"


def test_root_cause_is_the_earliest_error_and_upper_layers_are_consequences():
    # 実際の伝わり方: Tool が失敗 → Agent → Task → Crew → Flow の順に error になる
    trace = trace_with(("flow", "run_analysis", "error", 900), ("crew", "crew", "error", 800), ("task", "t", "error", 700),
                       ("agent", "a", "error", 600), ("tool", "get_sales_by_region", "error", 500))
    assert classify_failure("escalated", "transient", trace.events) == "tool"


def test_llm_failure_is_found_when_no_tool_failed():
    trace = trace_with(("llm", "gpt-4o-mini", "error", 300), ("agent", "a", "error", 310), ("crew", "crew", "error", 320))
    assert classify_failure("escalated", "transient", trace.events) == "llm"


def test_same_instant_errors_pick_the_lower_layer():
    trace = trace_with(("agent", "a", "error", 500), ("tool", "t", "error", 500))
    assert classify_failure("escalated", "transient", trace.events) == "tool"


def test_failure_without_a_causing_event_is_the_service_layer():
    assert classify_failure("error", "", []) == "service"
    assert classify_failure("escalated", "transient", trace_with(("flow", "receive", "ok", 10)).events) == "service"


def test_non_error_statuses_are_not_causes():
    trace = trace_with(("flow", "request_approval", "paused", 100), ("agent", "a", "error", 400))
    assert classify_failure("escalated", "transient", trace.events) == "agent"


# --- 実行の記録 ---

def test_record_summarises_trace_and_outcome_without_any_content():
    trace = trace_with(("llm", "gpt-4o-mini", "ok", 100, {"prompt_tokens": 300, "completion_tokens": 50}),
                       ("tool", "get_sales_by_region", "error", 200, {"error": "boom"}),
                       ("tool", "get_sales_by_region", "ok", 300), ("attempt", "analysis", "ok", 900))
    outcome = FlowOutcome("analysis", "single", None, message="売上は sk-secret12345678", attempts=1)
    rec = build_run_record("run000000001", outcome, trace, now=5.0)
    assert (rec.outcome, rec.route, rec.attempts, rec.llm_calls, rec.total_tokens) == ("analysis", "single", 1, 1, 350)
    assert rec.tool_calls == ("get_sales_by_region",) * 2  # 失敗した呼び出しも、呼び出しの数に含まれる
    assert rec.tool_errors == 1 and rec.failure_layer == "" and rec.first_pass  # 立て直した Tool の失敗は、成功の中に残る
    text = json.dumps(rec.to_log(), ensure_ascii=False)
    assert "sk-secret" not in text and "boom" not in text and "売上は" not in text


def test_record_fields_are_a_fixed_safe_allowlist():
    fields = {f.name for f in dataclasses.fields(RunRecord)}
    assert not fields & {"question", "message", "result", "summary", "error", "args", "output", "actor_id"}


def test_exception_in_the_run_becomes_an_error_record():
    rec = build_run_record("run000000001", None, trace_with(), now=5.0)
    assert (rec.outcome, rec.failure_layer, rec.route) == ("error", "service", "unknown") and rec.failed


# --- Metric ---

def value(observer, sample, **labels):
    for family in observer.registry.collect():
        for s in family.samples:
            if s.name == sample and s.labels == labels:
                return s.value
    return None


def test_record_run_updates_the_metrics():
    o = Observer(known_tools={"get_sales_by_region"})
    o.record_run(record(tool_calls=("get_sales_by_region", "get_sales_by_region"), tool_errors=1, llm_calls=2,
                        prompt_tokens=300, completion_tokens=50, attempts=2, duration_ms=3000))
    o.record_run(record(outcome="escalated", failure_layer="llm", status="error"))
    assert value(o, "analyses_total", route="single", outcome="analysis") == 1
    assert value(o, "analyses_total", route="single", outcome="escalated") == 1
    assert value(o, "analysis_failures_total", layer="llm") == 1
    assert value(o, "tool_calls_total", tool="get_sales_by_region") == 2
    assert value(o, "tool_errors_total") == 1 and value(o, "analysis_retries_total") == 1
    assert value(o, "llm_tokens_total", type="prompt") == 300 and value(o, "llm_calls_total") == 2
    assert value(o, "analysis_duration_seconds_count", route="single") == 2
    assert value(o, "analysis_duration_seconds_bucket", route="single", le="5.0") == 2  # 3.0秒と2.0秒


def test_labels_are_bounded_no_matter_what_the_run_contains():
    o = Observer(known_tools={"get_sales_by_region"})
    for i in range(50):
        o.record_run(record(run_id=f"id{i:010d}", route=f"route-{i}", tool_calls=(f"tool_{i}",), failure_layer="weird" if i % 2 else ""))
    text = o.render().decode()
    assert "id0000000001" not in text  # run_id は、ラベルにも値にも出ない
    assert value(o, "analyses_total", route=OTHER, outcome="analysis") == 50
    assert value(o, "tool_calls_total", tool=OTHER) == 50
    assert value(o, "analysis_failures_total", layer=OTHER) == 25
    assert len(re.findall(r"^analyses_total\{", text, re.M)) == 1  # 50回の実行でも、系列は1つ


def test_http_metrics_use_the_given_route_and_status():
    o = Observer()
    o.record_request("POST", "/v1/analyses", 429, 0.01)
    o.record_request("POST", "/v1/analyses", 200, 1.5)
    assert value(o, "http_requests_total", method="POST", route="/v1/analyses", status="429") == 1
    assert value(o, "http_request_duration_seconds_count", route="/v1/analyses") == 2


def test_render_is_prometheus_text():
    o = Observer()
    o.record_run(record())
    text = o.render().decode()
    assert "# TYPE analyses_total counter" in text and 'analyses_total{outcome="analysis",route="single"} 1.0' in text


# --- 実行の記録の出力と、直近の窓 ---

def test_run_record_is_written_to_standard_output_as_one_json_line(capsys):
    configure_run_logging()
    configure_run_logging()  # 2回呼んでも、二重に出ない
    Observer().record_run(record(run_id="abc123456789"))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["event"] == "run_completed" and logged["run_id"] == "abc123456789" and logged["tool_calls"] == []


def test_window_returns_only_recent_records_and_is_bounded():
    o = Observer(window_size=3)
    for ts in (100.0, 200.0, 300.0, 400.0):
        o.record_run(record(ts=ts))
    assert [r.ts for r in o.runs_since(0)] == [200.0, 300.0, 400.0]  # 古いものから捨てる
    assert [r.ts for r in o.runs_since(300.0)] == [300.0, 400.0]
