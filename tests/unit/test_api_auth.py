"""API の認証・認可・アクセスの記録・本番の起動（第16章）のテスト。LLM は呼ばない。承認 Flow と SQLite は本物（一時ファイル）。"""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import shared.api as api
import shared.review_flow as review_flow
from shared.analysis_flow import FlowOutcome
from shared.auth import Authenticator, credential_to_dict, new_credential
from shared.config import ConfigError
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory

QUESTION = "地域別の売上を比較してください。"


def result():
    return SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


@pytest.fixture
def env(tmp_path):
    tokens, creds = {}, []
    now = datetime.now(timezone.utc)
    for key, actor, roles, expires in (("alice", "user-001", ["analyst"], None), ("bob", "user-002", ["analyst"], None),
                                       ("approver", "approver-001", ["approver"], None), ("approver2", "approver-002", ["approver"], None), ("both", "user-003", ["analyst", "approver"], None),
                                       ("expired", "user-004", ["analyst"], now - timedelta(minutes=1))):
        tokens[key], cred = new_credential(f"cred-{key}", actor, roles, expires)
        creds.append(cred)
    calls = []

    def run_question(question, actor_id, **kwargs):
        calls.append(actor_id)
        return FlowOutcome("analysis", "single", result(), attempts=1)

    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=run_question,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "t",
                            authenticator=Authenticator(creds))
    client = TestClient(api.create_app(deps), raise_server_exceptions=False)
    return type("E", (), {"client": client, "tokens": tokens, "calls": calls, "deps": deps})()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def analyze(env, who="alice", actor="user-001", token=None):
    return env.client.post("/v1/analyses", json={"question": QUESTION, "actor_id": actor}, headers=h(token or env.tokens[who]))


# --- 認証（401）---

def test_requests_without_a_valid_token_are_rejected_before_any_work(env):
    for headers in ({}, h("wrong"), h(env.tokens["expired"]), {"Authorization": "Basic abc"}):
        r = env.client.post("/v1/analyses", json={"question": QUESTION, "actor_id": "user-001"}, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated" and r.headers["WWW-Authenticate"] == "Bearer"
    assert env.calls == []


def test_authentication_failures_are_indistinguishable_to_the_caller(env):
    """トークンがない・不正・期限切れを、応答から区別できない（有効なトークンを探る手がかりを、渡さない）。"""
    bodies = {json.dumps(env.client.get("/v1/me", headers=headers).json()) for headers in ({}, h("wrong"), h(env.tokens["expired"]))}
    assert len(bodies) == 1


def test_valid_token_works_and_me_reports_the_identity(env):
    assert analyze(env).status_code == 200 and env.calls == ["user-001"]
    me = env.client.get("/v1/me", headers=h(env.tokens["approver"])).json()
    assert me == {"auth_required": True, "actor_id": "approver-001", "roles": ["approver"], "credential_id": "cred-approver"}


def test_me_without_authentication_enabled_reports_development_mode(tmp_path):
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", preference_store_factory=lambda: None)
    assert TestClient(api.create_app(deps)).get("/v1/me").json() == {"auth_required": False, "actor_id": None, "roles": [], "credential_id": None}


# --- 認可（403）---

def test_a_user_cannot_act_under_someone_elses_name(env):
    r = analyze(env, who="alice", actor="user-002")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden" and env.calls == []


def test_roles_separate_analysts_from_approvers(env):
    assert env.client.get("/v1/reviews/pending", headers=h(env.tokens["alice"])).status_code == 403
    r = env.client.post("/v1/analyses", json={"question": QUESTION, "actor_id": "approver-001"}, headers=h(env.tokens["approver"]))
    assert r.status_code == 403 and env.calls == []  # 承認者は、分析の役割を持たない
    assert env.client.get("/v1/reviews/pending", headers=h(env.tokens["approver"])).status_code == 200


def test_forbidden_does_not_say_which_check_failed(env):
    wrong_actor = analyze(env, who="alice", actor="user-002").json()["error"]["message"]
    wrong_role = env.client.get("/v1/reviews/pending", headers=h(env.tokens["alice"])).json()["error"]["message"]
    assert wrong_actor == wrong_role


def test_preferences_are_private_to_their_owner(env):
    assert env.client.post("/v1/actors/user-001/preferences", json={"content": "結論から書く"}, headers=h(env.tokens["alice"])).status_code == 201
    assert env.client.get("/v1/actors/user-001/preferences", headers=h(env.tokens["bob"])).status_code == 403
    assert env.client.delete("/v1/actors/user-001/preferences/id0", headers=h(env.tokens["bob"])).status_code == 403
    assert env.client.post("/v1/actors/user-001/preferences", json={"content": "x"}, headers=h(env.tokens["bob"])).status_code == 403
    assert len(env.client.get("/v1/actors/user-001/preferences", headers=h(env.tokens["alice"])).json()["items"]) == 1


def test_approval_identity_comes_from_the_credential_not_from_the_request(env):
    ticket = review_flow.start_review("O0001", "user-001", env.deps.review_store, env.deps.flow_db)
    url = f"/v1/reviews/{ticket.flow_id}/decision"
    # 承認者を名乗っても、認証された本人と違えば拒否
    spoof = env.client.post(url, json={"decision": "approve", "approver_id": "approver-001"}, headers=h(env.tokens["alice"]))
    assert spoof.status_code == 403
    assert env.deps.review_store.list_requests() == []
    ok = env.client.post(url, json={"decision": "approve", "approver_id": "approver-001"}, headers=h(env.tokens["approver"]))
    assert ok.status_code == 200 and ok.json()["result"] == "registered"
    [row] = env.deps.review_store.list_requests()
    assert row["approved_by"] == "approver-001" and row["requested_by"] == "user-001"
    assert [e["actor"] for e in env.deps.review_store.audit_log(ticket.flow_id) if e["event"] == "approved"] == ["approver-001"]


def test_an_approver_cannot_approve_in_another_approvers_name(env):
    """承認者の役割を持っていても、他の承認者の名前で承認できない（監査ログの承認者が、偽れない）。"""
    ticket = review_flow.start_review("O0001", "user-001", env.deps.review_store, env.deps.flow_db)
    r = env.client.post(f"/v1/reviews/{ticket.flow_id}/decision", json={"decision": "approve", "approver_id": "approver-001"},
                        headers=h(env.tokens["approver2"]))
    assert r.status_code == 403 and env.deps.review_store.list_requests() == []
    assert [p["flow_id"] for p in env.deps.review_store.pending_approvals()] == [ticket.flow_id]  # 承認待ちのまま


def test_requester_with_the_approver_role_still_cannot_approve_own_request(env):
    """役割があっても、職務の分離は残る。依頼した本人は、承認できない。"""
    ticket = review_flow.start_review("O0001", "user-003", env.deps.review_store, env.deps.flow_db)
    r = env.client.post(f"/v1/reviews/{ticket.flow_id}/decision", json={"decision": "approve", "approver_id": "user-003"}, headers=h(env.tokens["both"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_approval"
    assert env.deps.review_store.list_requests() == []


# --- 公開してよいもの / しないもの ---

def test_probes_are_public_and_the_api_description_is_not(env):
    assert env.client.get("/healthz").status_code == 200 and env.client.get("/readyz").status_code in (200, 503)
    assert env.client.get("/docs").status_code == 404 and env.client.get("/openapi.json").status_code == 404


# --- アクセスの記録（Audit）---

def records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "audit"]


def test_access_log_records_who_did_what_without_secrets_or_content(env, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        ok = analyze(env)
        analyze(env, who="alice", actor="user-002")
        env.client.get("/v1/me", headers=h("wrong-token-value"))
    ok_log, forbidden_log, unauth_log = records(caplog)
    assert ok_log["status"] == 200 and ok_log["credential_id"] == "cred-alice" and ok_log["actor_id"] == "user-001"
    assert ok_log["run_id"] == ok.headers["X-Run-Id"] and ok_log["route"] == "/v1/analyses"
    assert forbidden_log["status"] == 403 and forbidden_log["credential_id"] == "cred-alice" and forbidden_log["reason"] == "actor_mismatch"
    assert unauth_log["status"] == 401 and unauth_log["credential_id"] is None and unauth_log["reason"] == "unknown_token"
    text = caplog.text
    assert env.tokens["alice"] not in text and "wrong-token-value" not in text and "地域別" not in text and "Bearer" not in text


def test_probes_are_not_written_to_the_access_log(env, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        env.client.get("/healthz")
    assert records(caplog) == []


# --- 本番の起動 ---

def test_production_startup_refuses_an_unsafe_configuration(monkeypatch):
    for name in ("AUTH_CREDENTIALS", "AUTH_CREDENTIALS_FILE", "DATABASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConfigError, match="auth_missing"):
        api.create_app()


def test_production_startup_with_secret_files_enables_authentication(monkeypatch, tmp_path):
    token, cred = new_credential("c1", "user-001", ["analyst"])
    (tmp_path / "creds").write_text(json.dumps([credential_to_dict(cred)]))
    (tmp_path / "db").write_text("postgresql://app:" + "x" * 20 + "@db:5432/sales")
    (tmp_path / "key").write_text("sk-test-0000")
    for name in ("AUTH_CREDENTIALS", "DATABASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    monkeypatch.setenv("AUTH_CREDENTIALS_FILE", str(tmp_path / "creds"))
    monkeypatch.setenv("DATABASE_URL_FILE", str(tmp_path / "db"))
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(tmp_path / "key"))
    stores = dict(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", preference_store_factory=lambda: None)
    deps = api.Dependencies.from_environment(**stores)  # 検査と資格情報の読み込み（ストアは、DB へ接続しない偽の置き換え）
    client = TestClient(api.create_app(deps), raise_server_exceptions=False)
    assert deps.authenticator is not None and client.get("/docs").status_code == 404
    assert client.get("/v1/me").status_code == 401 and client.get("/v1/me", headers=h(token)).json()["actor_id"] == "user-001"


def test_secret_values_read_from_files_reach_the_environment_but_not_the_logs(monkeypatch, tmp_path, caplog):
    (tmp_path / "key").write_text("sk-this-is-a-secret-value")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(tmp_path / "key"))
    with caplog.at_level(logging.DEBUG):
        api.config.apply_secret_files()
    import os
    assert os.environ["OPENAI_API_KEY"] == "sk-this-is-a-secret-value" and "sk-this-is-a-secret-value" not in caplog.text
    monkeypatch.delenv("OPENAI_API_KEY")


def test_access_log_is_actually_written_to_standard_output(env, capsys):
    """caplog は、ロガーの設定を上書きして拾う。本番で、ログが出力されること（標準出力へ、1行1件の JSON）を、別に確かめる。"""
    analyze(env)
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    record = json.loads(line)
    assert record["event"] == "api_access" and record["credential_id"] == "cred-alice" and record["status"] == 200
    assert datetime.fromisoformat(record["ts"]).tzinfo is not None  # いつ（UTC）。ログの収集側の時刻に頼らない
