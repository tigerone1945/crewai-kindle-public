"""LLMを使う統合テスト（APIキーが必要）。第14章: 分析サービスの API を、本物の Flow・Crew・LLM で確かめる。

出力は確率的。ここで確かめるのは、HTTP の契約（ステータス・エラーの形・Trace の保存）と、LLM を呼ばない経路。
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

import shared.api as api
from shared.api_contract import AnalysisResponse, ErrorResponse
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from tests.fakes import FakeMemory
from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not (ROOT / ".env").exists(), reason="LLMを呼ぶためAPIキーが必要"
)


@pytest.fixture
def env(tmp_path):
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "review.db"), flow_db=tmp_path / "flows.db",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "traces")
    return type("Env", (), {"deps": deps, "client": TestClient(api.create_app(deps)), "traces": tmp_path / "traces"})()


def trace_of(env, response):
    return json.loads((env.traces / f"{response.headers['X-Run-Id']}.json").read_text(encoding="utf-8"))


def analyze(env, question):
    for _ in range(2):  # まれな失敗（Guardrail の使い切り＝502）は、全体を1回だけ再実行して吸収する
        r = env.client.post("/v1/analyses", json={"question": question, "actor_id": "user-001"})
        if r.status_code != 502:
            break
    return r


def test_analysis_returns_a_contract_response_and_saves_the_trace_without_the_question(env):
    r = analyze(env, "地域別の売上を比較してください。")
    assert r.status_code == 200
    body = AnalysisResponse.model_validate(r.json())
    assert body.kind == "analysis" and body.route == "single" and body.result.metrics
    trace = trace_of(env, r)
    assert "get_sales_by_region" in trace["summary"]["tool_calls"] and "地域別" not in json.dumps(trace, ensure_ascii=False)


def test_quality_question_runs_the_three_roles_through_the_api(env):
    r = analyze(env, "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
    assert r.status_code == 200 and r.json()["route"] == "integrated" and r.json()["result"]["analysis_type"] == "integrated_analysis"
    assert len(trace_of(env, r)["summary"]["roles"]) == 3


def test_out_of_scope_is_422_without_calling_the_llm(env):
    r = env.client.post("/v1/analyses", json={"question": "Pythonでゲームを作ってください。", "actor_id": "user-001"})
    assert r.status_code == 422 and ErrorResponse.model_validate(r.json()).error.code == "input_rejected"
    assert trace_of(env, r)["summary"]["llm_calls"] == 0


def test_review_request_is_202_without_calling_the_llm(env):
    r = env.client.post("/v1/analyses", json={"question": "O0001をレビュー対象として登録してください。", "actor_id": "user-001"})
    assert r.status_code == 202 and r.json()["kind"] == "review_pending"
    assert trace_of(env, r)["summary"]["llm_calls"] == 0 and env.deps.review_store.list_requests() == []
