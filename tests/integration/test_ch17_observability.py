"""LLMを使う統合テスト（APIキーが必要）。第17章: 本物の Flow・Crew・LLM の実行が、運用の記録（Log・Metric・失敗の層）になるか。

出力は確率的。ここで確かめるのは、記録の項目（Tool・LLM・Token の件数）と、故障を入れたときの、根本の層の判定。
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

import shared.api as api
import shared.sales_db as sales_db
from shared.memory_policy import PreferenceStore
from shared.observability import Observer
from shared.review_requests import ReviewStore
from tests.fakes import FakeMemory
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)

QUESTION = "地域別の売上を比較してください。"


@pytest.fixture
def env(tmp_path):
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "review.db"), flow_db=tmp_path / "flows.db",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "traces",
                            observer=Observer())
    return type("Env", (), {"deps": deps, "client": TestClient(api.create_app(deps), raise_server_exceptions=False),
                            "traces": tmp_path / "traces"})()


def analyze(env, question=QUESTION):
    r = env.client.post("/v1/analyses", json={"question": question, "actor_id": "user-001"})
    return r, env.deps.observer.runs_since(0)[-1]


def test_real_run_becomes_a_record_with_tools_llm_calls_tokens_and_check_time(env, capsys):
    r, rec = analyze(env)
    assert r.status_code == 200 and rec.run_id == r.headers["X-Run-Id"]
    assert rec.outcome == "analysis" and rec.failure_layer == "" and rec.route == "single"
    assert "get_sales_by_region" in rec.tool_calls and rec.llm_calls >= 1 and rec.prompt_tokens > 0 and rec.completion_tokens > 0
    trace = json.loads((env.traces / f"{rec.run_id}.json").read_text(encoding="utf-8"))
    checks = [e for e in trace["events"] if e["kind"] == "check"]
    assert checks and all(e["duration_ms"] is not None for e in checks)  # 検査の時間も、Trace にある
    metrics = env.client.get("/metrics").text
    assert 'analyses_total{outcome="analysis",route="single"} 1.0' in metrics and 'tool_calls_total{tool="get_sales_by_region"}' in metrics
    logged = capsys.readouterr().out
    assert "地域別" not in logged and "地域別" not in metrics and "地域別" not in json.dumps(trace, ensure_ascii=False)


def test_rejected_input_is_the_input_layer_and_costs_no_llm_call(env):
    r, rec = analyze(env, "今日の東京の天気を教えてください。")
    assert r.status_code == 422
    assert (rec.outcome, rec.failure_layer, rec.llm_calls, rec.prompt_tokens) == ("rejected", "input", 0, 0)
    assert not rec.failed  # システムの失敗ではない


def test_invalid_llm_key_is_attributed_to_the_llm_layer(env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-invalid-key-for-the-test")
    r, rec = analyze(env)
    assert r.status_code == 503
    assert (rec.outcome, rec.failure_layer, rec.error_kind) == ("escalated", "llm", "transient") and rec.attempts == 3 and rec.failed


def test_failing_tool_is_attributed_to_the_tool_layer_not_to_the_crew_or_flow(env, monkeypatch):
    def unreachable(self, *a, **k):
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(sales_db.SalesRepository, "sales_by", unreachable)
    r, rec = analyze(env)
    trace = json.loads((env.traces / f"{rec.run_id}.json").read_text(encoding="utf-8"))
    upper = {e["kind"] for e in trace["events"] if e["status"] == "error"} - {"tool"}
    assert upper  # 上の層（Agent・Crew・Flow）にも error が伝わっている。それでも、根本は Tool
    assert r.status_code == 503 and rec.failure_layer == "tool" and rec.tool_errors >= 1
