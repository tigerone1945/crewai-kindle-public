"""分析サービスの API（第14章）のテスト。LLM は呼ばない（run_question は偽物）。承認 Flow と SQLite は本物（一時ファイル）。"""
import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import shared.api as api
import shared.review_flow as review_flow
from shared.analysis_flow import FlowOutcome
from shared.api_contract import AnalysisResponse, ErrorResponse
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from shared.tracing import RunTrace
from tests.fakes import FakeMemory

QUESTION = "地域別の売上を比較してください。"


def result(analysis_type="region_sales"):
    return SalesAnalysisResult(question="q", analysis_type=analysis_type, summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


class Fake:
    """run_question の偽物。呼ばれた引数を残し、決めた結果か例外を返す。"""

    def __init__(self, outcome=None):
        self.outcome = outcome or FlowOutcome("analysis", "single", result(), attempts=1)
        self.calls = []

    def __call__(self, question, actor_id, **kwargs):
        self.calls.append(dict(question=question, actor_id=actor_id, **kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "review.db"), flow_db=tmp_path / "flows.db",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), run_question=fake,
                            trace_dir=tmp_path / "traces")
    app = api.create_app(deps)
    return type("Env", (), {"fake": fake, "deps": deps, "app": app, "client": TestClient(app, raise_server_exceptions=False),
                            "tmp": tmp_path})()


def post(env, question=QUESTION, actor_id="user-001", **extra):
    return env.client.post("/v1/analyses", json={"question": question, "actor_id": actor_id, **extra})


# --- 契約: 成功 ---

def test_successful_analysis_follows_the_contract(env):
    r = post(env)
    assert r.status_code == 200
    body = AnalysisResponse.model_validate(r.json())  # 契約の型で読める
    assert body.kind == "analysis" and body.route == "single" and body.result.summary == "東北が最も高い売上です"
    assert r.headers["X-Run-Id"] == body.run_id and len(body.run_id) == 12
    assert "review_flow_id" not in r.json()  # 該当しない項目は出さない


def test_history_and_actor_are_passed_to_the_flow(env):
    post(env, history=[{"question": "売上は？", "analysis_type": "sales_summary", "summary": "要約"}])
    call = env.fake.calls[0]
    assert call["actor_id"] == "user-001" and call["history"][0].summary == "要約"


def test_preferences_are_looked_up_on_the_server_side(env):
    env.deps.preference_store().remember_preference("user-001", "結論から書く")
    post(env)
    assert env.fake.calls[0]["preferences_for"](QUESTION) == ["結論から書く"]  # 利用者IDで絞った、その人の好みだけ
    env.fake.calls.clear()
    post(env, actor_id="user-002")
    assert env.fake.calls[0]["preferences_for"](QUESTION) == []


def test_review_request_is_accepted_with_202(env):
    env.fake.outcome = FlowOutcome("review_pending", "review_request", message="承認待ちにしました", review_flow_id="abcd1234-ef")
    r = post(env, "O0001をレビュー対象として登録してください。")
    assert r.status_code == 202 and r.json()["kind"] == "review_pending" and r.json()["review_flow_id"] == "abcd1234-ef"
    assert "result" not in r.json()


# --- 契約: 入力 ---

@pytest.mark.parametrize("payload", [
    {"question": "", "actor_id": "user-001"},
    {"question": "x" * 2001, "actor_id": "user-001"},
    {"question": "売上", "actor_id": "../etc/passwd"},
    {"question": "売上", "actor_id": "user-001", "unknown": 1},
    {"question": "売上", "actor_id": "user-001", "history": [{"question": "q", "analysis_type": "a", "summary": "s"}] * 6},
    {"actor_id": "user-001"},
])
def test_invalid_requests_are_rejected_with_422_and_never_reach_the_flow(env, payload):
    r = env.client.post("/v1/analyses", json=payload)
    assert r.status_code == 422 and ErrorResponse.model_validate(r.json()).error.code == "invalid_request"
    assert env.fake.calls == []


def test_validation_errors_do_not_echo_the_input(env):
    """FastAPI の既定は、入力の値を応答に含める。秘密情報が入った質問を、そのまま返してしまう。"""
    secret = "API key: sk-abcdef123456 " + "x" * 2001
    r = env.client.post("/v1/analyses", json={"question": secret, "actor_id": "user-001"})
    assert r.status_code == 422 and "sk-abcdef" not in r.text and "xxxx" not in r.text


def test_malformed_json_is_a_contract_error(env):
    r = env.client.post("/v1/analyses", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


# --- エラーモデル ---

@pytest.mark.parametrize("outcome, status, code", [
    (FlowOutcome("rejected", "rejected", message="この質問には対応できません。", reason="out_of_scope"), 422, "input_rejected"),
    (FlowOutcome("review_invalid", "review_request", message="1件ずつ登録してください。"), 422, "review_invalid"),
    (FlowOutcome("escalated", "single", message="質問の表現を変えて", error_kind="guardrail"), 502, "analysis_failed"),
    (FlowOutcome("escalated", "single", message="時間をおいて", error_kind="transient"), 503, "unavailable"),
])
def test_flow_outcomes_map_to_the_error_model(env, outcome, status, code):
    env.fake.outcome = outcome
    r = post(env)
    err = ErrorResponse.model_validate(r.json()).error
    assert r.status_code == status and err.code == code and err.message == outcome.message
    assert err.run_id == r.headers["X-Run-Id"]
    assert ("Retry-After" in r.headers) == (code == "unavailable")


def test_unexpected_failure_returns_500_without_details(env):
    env.fake.outcome = RuntimeError("sqlite3.OperationalError: /Users/x/secret.db secret-host:5432")
    r = post(env)
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal"
    assert "secret" not in r.text and "sqlite" not in r.text and r.json()["error"]["run_id"]


def test_unknown_route_gets_the_standard_error_shape(env):
    r = env.client.get("/v1/nothing")
    assert r.status_code == 404  # フレームワーク標準の 404。本文は契約の対象外（API の利用側は、既知の経路だけを呼ぶ）


# --- Trace ---

def test_trace_is_saved_under_the_run_id_and_without_the_question(env):
    r = post(env, "地域別の売上を比較してください。")
    path = env.tmp / "traces" / f"{r.json()['run_id']}.json"
    assert path.exists() and "地域別" not in path.read_text(encoding="utf-8")


def test_trace_save_failure_does_not_fail_the_request(env, monkeypatch):
    def disk_full(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(RunTrace, "save", disk_full)
    assert post(env).status_code == 200


# --- 同時実行・時間切れ ---

def test_concurrency_limit_returns_429_and_frees_the_slot_afterwards(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(question, actor_id, **kwargs):
        started.set()
        release.wait(10)
        return FlowOutcome("analysis", "single", result(), attempts=1)

    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=slow,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), max_concurrent=1,
                            trace_dir=tmp_path / "t")
    app = api.create_app(deps)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = {"question": QUESTION, "actor_id": "user-001"}
            first = asyncio.create_task(c.post("/v1/analyses", json=body))
            while not started.is_set():
                await asyncio.sleep(0.01)
            busy = await c.post("/v1/analyses", json=body)
            health = await c.get("/healthz")  # 分析の実行中も、イベントループは止まらない
            release.set()
            done = await first
            again = await c.post("/v1/analyses", json=body)
            return busy, health, done, again

    busy, health, done, again = asyncio.run(scenario())
    assert busy.status_code == 429 and busy.json()["error"]["code"] == "busy" and busy.headers["Retry-After"]
    assert health.status_code == 200 and health.json()["running"] == 1
    assert done.status_code == 200 and again.status_code == 200  # 終わったら、枠が空く


def test_timeout_returns_504_but_keeps_the_slot_until_the_thread_finishes(tmp_path):
    release = threading.Event()

    def slow(question, actor_id, **kwargs):
        release.wait(10)
        return FlowOutcome("analysis", "single", result(), attempts=1)

    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=slow,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), max_concurrent=1,
                            request_timeout=0.2, trace_dir=tmp_path / "t")
    app = api.create_app(deps)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            body = {"question": QUESTION, "actor_id": "user-001"}
            timed_out = await c.post("/v1/analyses", json=body)
            still_busy = await c.post("/v1/analyses", json=body)  # スレッドはまだ動いている。枠は空かない
            release.set()
            for _ in range(100):
                if (await c.get("/healthz")).json()["running"] == 0:
                    break
                await asyncio.sleep(0.02)
            freed = await c.post("/v1/analyses", json=body)
            return timed_out, still_busy, freed

    timed_out, still_busy, freed = asyncio.run(scenario())
    assert timed_out.status_code == 504 and timed_out.json()["error"]["code"] == "timeout"
    assert still_busy.status_code == 429
    assert freed.status_code == 200


def test_blocking_work_does_not_block_other_requests(tmp_path):
    """分析（同期・ブロッキング）が実行中でも、別のリクエスト（/healthz）にすぐ答える。"""
    def slow(question, actor_id, **kwargs):
        time.sleep(0.6)
        return FlowOutcome("analysis", "single", result(), attempts=1)

    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=slow,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "t")
    app = api.create_app(deps)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            t0 = time.perf_counter()  # sleep の前から測る。ブロッキングなら、sleep 自体が分析の終わりまで長引く
            running = asyncio.create_task(c.post("/v1/analyses", json={"question": QUESTION, "actor_id": "user-001"}))
            await asyncio.sleep(0.1)
            await c.get("/healthz")
            latency = time.perf_counter() - t0 - 0.1
            await running
            return latency

    assert asyncio.run(scenario()) < 0.3  # ブロッキングなら、0.5 秒ほど待たされる


# --- 承認（本物の承認 Flow）---

@pytest.fixture
def review_env(tmp_path):
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "review.db"), flow_db=tmp_path / "flows.db",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "traces")
    app = api.create_app(deps)
    client = TestClient(app, raise_server_exceptions=False)
    ticket = review_flow.start_review("O0001", "user-001", deps.review_store, deps.flow_db)
    return type("Env", (), {"deps": deps, "client": client, "ticket": ticket})()


def test_pending_reviews_lists_the_order_facts_without_names(review_env):
    r = review_env.client.get("/v1/reviews/pending")
    [item] = r.json()["items"]
    assert item["order_id"] == "O0001" and item["flow_id"] == review_env.ticket.flow_id and item["order"]["sales_amount"] == 264600
    assert "customer_name" not in item["order"]


def test_approval_by_another_person_registers_the_request(review_env):
    r = review_env.client.post(f"/v1/reviews/{review_env.ticket.flow_id}/decision", json={"decision": "approve", "approver_id": "approver-001"})
    assert r.status_code == 200 and r.json()["result"] == "registered"
    assert [x["order_id"] for x in review_env.deps.review_store.list_requests()] == ["O0001"]
    assert review_env.client.get("/v1/reviews/pending").json()["items"] == []


def test_requester_cannot_approve_own_request(review_env):
    r = review_env.client.post(f"/v1/reviews/{review_env.ticket.flow_id}/decision", json={"decision": "approve", "approver_id": "user-001"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_approval"
    assert review_env.deps.review_store.list_requests() == []


def test_second_decision_is_a_conflict(review_env):
    url = f"/v1/reviews/{review_env.ticket.flow_id}/decision"
    review_env.client.post(url, json={"decision": "reject", "approver_id": "approver-001"})
    r = review_env.client.post(url, json={"decision": "approve", "approver_id": "approver-002"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_resolved"
    assert review_env.deps.review_store.list_requests() == []


@pytest.mark.parametrize("payload", [{"decision": "maybe", "approver_id": "a"}, {"decision": "approve"}, {"decision": "approve", "approver_id": "a b"}])
def test_decision_requires_an_exact_decision_and_a_valid_approver(review_env, payload):
    r = review_env.client.post(f"/v1/reviews/{review_env.ticket.flow_id}/decision", json=payload)
    assert r.status_code == 422 and review_env.deps.review_store.list_requests() == []


def test_flow_id_in_the_path_is_validated(review_env):
    r = review_env.client.post("/v1/reviews/..%2Fetc/decision", json={"decision": "approve", "approver_id": "approver-001"})
    assert r.status_code in (404, 422)


# --- 好み ---

def test_preferences_can_be_added_listed_and_deleted_per_actor(env):
    r = env.client.post("/v1/actors/user-001/preferences", json={"content": "要約は結論から書く"})
    assert r.status_code == 201
    [item] = r.json()["items"]
    assert env.client.get("/v1/actors/user-002/preferences").json()["items"] == []  # 他の利用者には見えない
    assert env.client.delete(f"/v1/actors/user-001/preferences/{item['id']}").status_code == 204
    assert env.client.get("/v1/actors/user-001/preferences").json()["items"] == []


@pytest.mark.parametrize("content", ["今月の売上は1,234,567円だった", "password=abc12345 を使う", "連絡先は a@example.com"])
def test_preferences_that_break_the_memory_policy_are_rejected(env, content):
    r = env.client.post("/v1/actors/user-001/preferences", json={"content": content})
    assert r.status_code == 422 and r.json()["error"]["code"] == "policy_violation"
    assert env.client.get("/v1/actors/user-001/preferences").json()["items"] == []


def test_invalid_actor_id_in_the_path_is_rejected(env):
    assert env.client.get("/v1/actors/bad%20id/preferences").status_code == 422


# --- 生存確認 ---

def test_healthz_does_not_touch_the_stores(env):
    env.deps.review_store = None  # DB に触れたら、失敗する
    r = env.client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "running": 0}


def test_openapi_documents_the_error_model(env):
    spec = env.client.get("/openapi.json").json()
    responses = spec["paths"]["/v1/analyses"]["post"]["responses"]
    assert {"200", "422", "429", "502", "503", "504"} <= set(responses)
    assert "ErrorResponse" in json.dumps(responses["429"])
