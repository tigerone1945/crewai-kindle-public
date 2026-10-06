"""API の運用の観測（第17章）のテスト: /metrics・/v1/ops/status・実行の記録・run_id の突き合わせ。LLM は呼ばない。"""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import shared.api as api
from shared.analysis_flow import FlowOutcome
from shared.auth import Authenticator, new_credential
from shared.memory_policy import PreferenceStore
from shared.observability import Observer
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from shared.tracing import RunTrace
from tests.fakes import FakeMemory

QUESTION = "顧客の山田太郎さんの売上を教えてください"  # 質問の文が、どこにも残らないことを確かめるための、特徴のある文


def result():
    return SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


class Fake:
    def __init__(self):
        self.outcome = FlowOutcome("analysis", "single", result(), attempts=1)
        self.delay = 0.0

    def __call__(self, question, actor_id, **kwargs):
        time.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def build(tmp_path, *, auth=False, **overrides):
    fake, tokens, authenticator = Fake(), {}, None
    if auth:
        creds = []
        for key, actor, roles in (("alice", "user-001", ["analyst"]), ("ops", "ops-001", ["operator"]), ("approver", "approver-001", ["approver"])):
            tokens[key], cred = new_credential(f"cred-{key}", actor, roles)
            creds.append(cred)
        authenticator = Authenticator(creds)
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=fake,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "t",
                            authenticator=authenticator, **overrides)
    client = TestClient(api.create_app(deps), raise_server_exceptions=False)
    return type("E", (), {"client": client, "fake": fake, "deps": deps, "tokens": tokens})()


@pytest.fixture
def env(tmp_path):
    return build(tmp_path)


@pytest.fixture
def secured(tmp_path):
    return build(tmp_path, auth=True)


def h(token):
    return {"Authorization": f"Bearer {token}"}


def post(env, headers=None, actor="user-001"):
    return env.client.post("/v1/analyses", json={"question": QUESTION, "actor_id": actor}, headers=headers or {})


def sample(env, name, **labels):
    for family in env.deps.observer.registry.collect():
        for s in family.samples:
            if s.name == name and s.labels == labels:
                return s.value
    return None


# --- 権限 ---

def test_metrics_and_status_require_the_operator_role(secured):
    for path in ("/metrics", "/v1/ops/status"):
        assert secured.client.get(path).status_code == 401
        assert secured.client.get(path, headers=h("wrong")).status_code == 401
        assert secured.client.get(path, headers=h(secured.tokens["alice"])).status_code == 403  # 分析者は、運用の数字を見られない
        assert secured.client.get(path, headers=h(secured.tokens["approver"])).status_code == 403
        assert secured.client.get(path, headers=h(secured.tokens["ops"])).status_code == 200


def test_operator_can_observe_but_cannot_analyze_or_approve(secured):
    ops = h(secured.tokens["ops"])
    assert secured.client.post("/v1/analyses", json={"question": QUESTION, "actor_id": "ops-001"}, headers=ops).status_code == 403
    assert secured.client.get("/v1/reviews/pending", headers=ops).status_code == 403


def test_without_authentication_metrics_are_open_for_development(env):
    assert env.client.get("/metrics").status_code == 200


# --- 実行の記録 ---

def test_analysis_updates_metrics_and_the_status(env):
    assert post(env).status_code == 200
    assert sample(env, "analyses_total", route="single", outcome="analysis") == 1
    assert sample(env, "http_requests_total", method="POST", route="/v1/analyses", status="200") == 1
    status = env.client.get("/v1/ops/status").json()
    assert status["runs_in_window"] == 1 and status["measures"]["first_pass_rate"] == {"value": 1.0, "samples": 1}
    assert status["alerts"] == []


def test_metrics_endpoint_serves_prometheus_text_without_any_content(env):
    post(env)
    r = env.client.get("/metrics")
    assert r.headers["content-type"].startswith("text/plain")
    assert 'analyses_total{outcome="analysis",route="single"} 1.0' in r.text
    for secret in (QUESTION, "山田", "user-001", "東北が最も高い売上です"):
        assert secret not in r.text


def test_run_id_ties_the_response_the_audit_log_the_run_log_and_the_trace_file(env, capsys):
    r = post(env)
    run_id = r.headers["X-Run-Id"]
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert {l["event"] for l in out if l["run_id"] == run_id} == {"api_access", "run_completed"}
    assert (env.deps.trace_dir / f"{run_id}.json").exists()
    assert QUESTION not in json.dumps(out, ensure_ascii=False)  # 質問は、どの記録にも残らない


def test_exception_in_the_run_is_recorded_as_a_service_failure(env):
    env.fake.outcome = RuntimeError("secret detail sk-abcdefgh12345")
    assert post(env).status_code == 500
    assert sample(env, "analyses_total", route="unknown", outcome="error") == 1
    assert sample(env, "analysis_failures_total", layer="service") == 1


def test_escalation_is_counted_with_its_layer(env):
    env.fake.outcome = FlowOutcome("escalated", "single", message="m", attempts=3, error_kind="guardrail")
    assert post(env).status_code == 502
    assert sample(env, "analysis_failures_total", layer="output_validation") == 1


def test_a_timed_out_run_is_still_recorded_when_it_finishes(tmp_path):
    env = build(tmp_path, request_timeout=0.05)
    env.fake.delay = 0.3
    assert post(env).status_code == 504
    deadline = time.time() + 5
    while sample(env, "analyses_total", route="single", outcome="analysis") is None and time.time() < deadline:
        time.sleep(0.05)
    assert sample(env, "analyses_total", route="single", outcome="analysis") == 1  # 利用者は504を受けたが、実行は続き、終わった時点で記録される
    assert sample(env, "http_requests_total", method="POST", route="/v1/analyses", status="504") == 1


def test_rejection_by_concurrency_limit_is_counted_as_http_429(tmp_path):
    env = build(tmp_path, max_concurrent=1)
    env.fake.delay = 0.4
    first = threading.Thread(target=post, args=(env,))
    first.start()
    time.sleep(0.15)
    assert post(env).status_code == 429
    first.join()
    assert sample(env, "http_requests_total", method="POST", route="/v1/analyses", status="429") == 1
    assert sample(env, "analyses_total", route="single", outcome="analysis") == 1  # 断った要求は、実行ではない


def test_in_flight_gauge_reports_running_analyses(tmp_path):
    env = build(tmp_path)
    env.fake.delay = 0.4
    t = threading.Thread(target=post, args=(env,))
    t.start()
    time.sleep(0.15)
    assert 'analyses_in_flight 1.0' in env.client.get("/metrics").text
    t.join()
    assert 'analyses_in_flight 0.0' in env.client.get("/metrics").text


def test_unknown_paths_share_one_route_label(env):
    for i in range(20):
        env.client.get(f"/no/such/path/{i}")
    assert sample(env, "http_requests_total", method="GET", route="unmatched", status="404") == 20


def test_a_failure_of_observation_does_not_break_the_answer(env, monkeypatch):
    monkeypatch.setattr(env.deps.observer, "record_run", lambda record: 1 / 0)
    assert post(env).status_code == 200


def test_trace_is_saved_even_when_recording_fails(env, monkeypatch):
    monkeypatch.setattr(env.deps.observer, "record_run", lambda record: 1 / 0)
    run_id = post(env).headers["X-Run-Id"]
    assert (env.deps.trace_dir / f"{run_id}.json").exists()


# --- Alert ---

def test_status_and_log_report_alerts_when_a_window_breaches_a_rule(env, capsys):
    env.fake.outcome = FlowOutcome("escalated", "single", message="m", attempts=3, error_kind="transient")
    for _ in range(10):
        post(env)
    status = env.client.get("/v1/ops/status").json()
    fired = {a["rule"]: a for a in status["alerts"]}
    assert {"system_failure_rate_high", "escalation_rate_high"} <= set(fired)
    assert fired["system_failure_rate_high"]["layer"] == "runtime_health" and fired["system_failure_rate_high"]["samples"] == 10
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    firing = [l for l in out if l["event"] == "alert_firing" and l["rule"] == "system_failure_rate_high"]
    assert len(firing) == 1  # 超え続けても、通知は1回


def test_alert_resolves_when_the_window_moves_on(tmp_path, capsys):
    env = build(tmp_path, alert_window_seconds=1)
    env.fake.outcome = FlowOutcome("escalated", "single", message="m", attempts=3, error_kind="transient")
    for _ in range(10):
        post(env)
    time.sleep(1.2)  # 失敗が、窓から出る
    env.fake.outcome = FlowOutcome("analysis", "single", result(), attempts=1)
    post(env)
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert any(l["event"] == "alert_resolved" and l["rule"] == "system_failure_rate_high" for l in out)
    assert env.client.get("/v1/ops/status").json()["alerts"] == []


def test_review_backlog_is_exposed_as_a_gauge(env):
    assert "reviews_pending 0.0" in env.client.get("/metrics").text


def test_ops_status_survives_a_failing_review_store(env, monkeypatch):
    monkeypatch.setattr(env.deps.review_store, "pending_approvals", lambda: 1 / 0)
    assert env.client.get("/v1/ops/status").status_code == 200
    assert env.client.get("/metrics").status_code == 200


# --- Trace の保存期間 ---

def test_old_traces_are_pruned_at_most_once_an_hour(tmp_path):
    import os
    env = build(tmp_path, trace_retention_days=1)
    env.deps.trace_dir.mkdir()

    def old_trace(name):
        path = env.deps.trace_dir / f"{name}.json"
        path.write_text("{}")
        os.utime(path, (0, 0))
        return path

    first, second = old_trace("a" * 12), None
    post(env)
    assert not first.exists()  # 最初の実行で、古いものを消す
    second = old_trace("b" * 12)
    post(env)
    assert second.exists()  # 1時間以内には、もう走らせない


def test_operational_settings_come_from_the_environment_and_bad_values_stop_startup(monkeypatch, tmp_path):
    from shared.config import ConfigError
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "3")
    monkeypatch.setenv("TRACE_MAX_FILES", "50")
    monkeypatch.setenv("ALERT_WINDOW_SECONDS", "600")
    deps = api.Dependencies.from_environment(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db")
    assert (deps.trace_retention_days, deps.trace_max_files, deps.alert_window_seconds) == (3, 50, 600)
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "abc")
    with pytest.raises(ConfigError):
        api.Dependencies.from_environment()
