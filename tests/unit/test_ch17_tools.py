"""運用の道具（第17章）のテスト: ログの集計、Trace の表示、Trace の削除。"""
import json
import os

import pytest

from shared.observability import RunRecord
from shared.tracing import RunTrace, prune_traces
from tests.helpers import load_chapter_module

ops = load_chapter_module("ch17-observability", "ops_report.py")
inspect_run = load_chapter_module("ch17-observability", "inspect_run.py")


def log_line(i=0, **kw):
    base = dict(run_id=f"{i:012x}", ts=1000.0 + i, route="single", outcome="analysis", status="ok", duration_ms=2000, attempts=1,
                prompt_tokens=400, completion_tokens=60, tool_calls=["get_sales_by_region"])
    return json.dumps({**base, **kw, "event": "run_completed"})


# --- ops_report ---

def test_report_reads_only_run_completed_lines():
    lines = [log_line(0), '{"event": "api_access", "status": 200}', "plain text", log_line(1, outcome="escalated", failure_layer="llm", status="error")]
    runs, skipped = ops.read_runs(lines)
    assert [r.outcome for r in runs] == ["analysis", "escalated"] and skipped == 0


def test_broken_run_lines_are_counted_not_silently_dropped():
    runs, skipped = ops.read_runs([log_line(0), '{"event": "run_completed", "run_id": ', '{"event": "run_completed", "oops": 1}'])
    assert len(runs) == 1 and skipped == 2
    assert "読めない行 2 件" in ops.report(runs, skipped)


def test_since_filters_old_runs():
    runs, _ = ops.read_runs([log_line(0), log_line(500)], since=1400.0)
    assert [r.ts for r in runs] == [1500.0]


def test_report_summarises_layers_latency_and_alerts():
    lines = [log_line(i) for i in range(6)] + [log_line(i, outcome="escalated", status="error", failure_layer="tool", attempts=3, duration_ms=30_000)
                                               for i in range(6, 12)]
    text = ops.report(ops.read_runs(lines)[0])
    assert "実行: 12 件" in text and "analysis 6" in text and "escalated 6" in text
    assert "失敗の層（根本）: tool 6" in text
    assert "p95 30.0 秒" in text
    assert "[runtime_health] system_failure_rate_high" in text and "[business_quality] escalation_rate_high" in text


def test_report_with_no_runs_says_so():
    assert "ありません" in ops.report([])


def test_report_has_no_alerts_for_a_healthy_window():
    text = ops.report(ops.read_runs([log_line(i) for i in range(12)])[0])
    assert "基準を超えている: なし" in text


def test_run_record_round_trips_through_the_log_format():
    rec = RunRecord(run_id="abc", ts=1.0, route="single", outcome="analysis", status="ok", duration_ms=5, tool_calls=("a", "b"))
    assert RunRecord.from_log(json.loads(json.dumps(rec.to_log()))) == rec
    assert RunRecord.from_log({**rec.to_log(), "future_field": 1}) == rec  # 未知の項目は無視


# --- inspect_run ---

def saved_trace(tmp_path, *events):
    trace = RunTrace("0123456789ab")
    for kind, name, status, at_ms, *rest in events:
        trace.add(kind, name, status, at=trace.started_at + at_ms / 1000, **(rest[0] if rest else {}))
    trace.save(tmp_path)
    return trace


def test_timeline_shows_the_first_error_as_the_root_cause(tmp_path):
    saved_trace(tmp_path, ("tool", "get_sales_by_region", "error", 400, {"error": "接続できません"}), ("agent", "a", "error", 450, {"error": "x"}),
                ("attempt", "analysis", "error", 500))
    text = inspect_run.timeline(inspect_run.load_trace("0123456789ab", tmp_path))
    assert "最初のエラー: tool" in text and "接続できません" in text and "get_sales_by_region" in text


def test_timeline_marks_recovered_errors(tmp_path):
    saved_trace(tmp_path, ("tool", "t", "error", 100, {"error": "x"}), ("tool", "t", "ok", 200), ("attempt", "analysis", "ok", 300))
    assert "回復して、最終的には成功" in inspect_run.timeline(inspect_run.load_trace("0123456789ab", tmp_path))


def test_load_trace_refuses_path_like_run_ids_and_reports_missing(tmp_path):
    (tmp_path.parent / "secret.json").write_text("{}")
    for bad in ("../secret", "..", "a/b", "ZZZZ", ""):
        with pytest.raises(ValueError):
            inspect_run.load_trace(bad, tmp_path)
    with pytest.raises(FileNotFoundError, match="保存期間"):
        inspect_run.load_trace("ffffffffffff", tmp_path)


# --- Trace の削除 ---

def make_files(tmp_path, ages_days, now=10_000_000.0):
    paths = []
    for i, age in enumerate(ages_days):
        p = tmp_path / f"{i:012x}.json"
        p.write_text("{}")
        os.utime(p, (now - age * 86400, now - age * 86400))
        paths.append(p)
    return paths


def test_prune_removes_expired_traces_only(tmp_path):
    old, new = make_files(tmp_path, [20, 1])
    assert prune_traces(tmp_path, max_age_days=14, now=10_000_000.0) == 1
    assert not old.exists() and new.exists()


def test_prune_keeps_the_newest_when_over_the_file_limit(tmp_path):
    paths = make_files(tmp_path, [5, 4, 3, 2, 1])
    assert prune_traces(tmp_path, max_age_days=14, max_files=2, now=10_000_000.0) == 3
    assert [p.exists() for p in paths] == [False, False, False, True, True]


def test_prune_never_touches_files_that_are_not_traces(tmp_path):
    other = tmp_path / "notes.json"
    other.write_text("keep")
    os.utime(other, (0, 0))
    make_files(tmp_path, [30])
    prune_traces(tmp_path, max_age_days=1, now=10_000_000.0)
    assert other.exists()


def test_prune_of_a_missing_directory_is_a_no_op(tmp_path):
    assert prune_traces(tmp_path / "none") == 0
