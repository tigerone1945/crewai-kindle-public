"""第16章の画面テスト（AppTest）。画面は、認証つきの本物の API だけを呼ぶ。LLM を呼ぶ分析サービスと Memory は偽物。"""
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import shared.analysis_flow as analysis_flow
import shared.analysis_service as service
import shared.api as api
import shared.api_client as api_client
from shared.auth import Authenticator, new_credential
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch16-production" / "ui.py")
REVIEW = "O0001をレビュー対象として登録してください。"


def result():
    return SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


def make_env(monkeypatch, tmp_path, authenticated=True):
    calls = []
    monkeypatch.setattr(service, "run_analysis", lambda question, history=(), preferences=(): calls.append(question) or result())
    monkeypatch.setattr(analysis_flow, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    tokens, creds = {}, []
    for key, actor, roles in (("alice", "user-001", ["analyst"]), ("approver", "approver-001", ["approver"]), ("both", "user-003", ["analyst", "approver"])):
        tokens[key], cred = new_credential(f"cred-{key}", actor, roles)
        creds.append(cred)
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", trace_dir=tmp_path / "t",
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()),
                            authenticator=Authenticator(creds) if authenticated else None)
    http = TestClient(api.create_app(deps), base_url="http://testserver", raise_server_exceptions=False)
    real = api_client.AnalysisApiClient
    monkeypatch.setattr(api_client, "AnalysisApiClient", lambda *a, token=None, **k: real(http=http, token=token))
    return type("E", (), {"tokens": tokens, "calls": calls, "deps": deps})()


@pytest.fixture
def env(monkeypatch, tmp_path):
    return make_env(monkeypatch, tmp_path)


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def text(at):
    return " ".join(e.value for e in [*at.markdown, *at.subheader, *at.warning, *at.error, *at.info, *at.success, *at.caption])


def login(at, token):
    at.text_input[0].set_value(token)
    next(b for b in at.button if b.label == "ログイン").click().run()
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    return at


def test_without_a_token_only_the_login_screen_is_shown(env):
    at = start()
    assert not at.exception and "ログイン" in text(at) and len(at.chat_input) == 0
    assert at.text_input[0].proto.type == 1  # TextInput.Type.PASSWORD: 入力が画面に表示されない
    assert env.calls == []


def test_wrong_token_is_refused_without_echoing_it(env):
    at = login(start(), "not-a-valid-token-xyz")
    assert "ログインできません" in text(at) and len(at.chat_input) == 0 and "not-a-valid-token-xyz" not in text(at)


def test_analyst_logs_in_and_the_identity_comes_from_the_server(env):
    at = login(start(), env.tokens["alice"])
    assert not at.exception and "ログイン中: user-001" in text(at) and "分析" in text(at)
    assert [t.label for t in at.text_input if t.label in ("利用者ID", "承認者ID")] == []  # IDは、名乗れない
    ask(at, "地域別の売上を比較してください。")
    assert "東北が最も高い売上です" in text(at) and env.calls == ["地域別の売上を比較してください。"]
    assert env.tokens["alice"] not in text(at)


def test_analyst_does_not_see_the_approval_list(env):
    other = login(start(), env.tokens["alice"])
    ask(other, REVIEW)
    assert "承認待ちのレビュー依頼" not in text(other) and "承認待ちにしました" in text(other)  # 依頼はできるが、承認はできない


def test_approver_sees_pending_requests_and_cannot_analyze(env):
    login(start(), env.tokens["alice"]).chat_input[0].set_value(REVIEW).run()
    at = login(start(), env.tokens["approver"])
    assert "承認待ちのレビュー依頼" in text(at) and "売上 264,600円" in text(at)
    assert at.chat_input[0].disabled and "分析の役割がありません" in text(at)


def test_approval_registers_the_request_under_the_authenticated_approver(env):
    login(start(), env.tokens["alice"]).chat_input[0].set_value(REVIEW).run()
    at = login(start(), env.tokens["approver"])
    button(at, "承認").click().run()
    assert "登録しました" in text(at)
    [row] = env.deps.review_store.list_requests()
    assert (row["requested_by"], row["approved_by"]) == ("user-001", "approver-001")


def test_requester_with_both_roles_cannot_approve_own_request(env):
    at = login(start(), env.tokens["both"])
    ask(at, REVIEW)
    button(at, "承認").click().run()
    assert "本人は承認できません" in text(at) and env.deps.review_store.list_requests() == []


def test_logout_returns_to_the_login_screen_and_forgets_the_conversation(env):
    at = login(start(), env.tokens["alice"])
    ask(at, "地域別の売上を比較してください。")
    button(at, "ログアウト").click().run()
    assert "ログイン" in text(at) and len(at.chat_input) == 0 and "東北が最も高い売上です" not in text(at)


def test_a_revoked_or_expired_credential_ends_the_session_with_a_notice(env):
    at = login(start(), env.tokens["alice"])
    env.deps.authenticator = Authenticator([])  # 失効（資格情報の削除）
    ask(at, "地域別の売上を比較してください。")
    assert "有効期限が切れました" in text(at) and len(at.chat_input) == 0 and env.calls == []


def test_without_authentication_the_development_screen_is_unchanged(monkeypatch, tmp_path):
    env = make_env(monkeypatch, tmp_path, authenticated=False)
    at = start()
    assert not at.exception and "認証なし（開発用）" in text(at) and [t.label for t in at.text_input][:2] == ["利用者ID", "承認者ID"]
    ask(at, "地域別の売上を比較してください。")
    assert env.calls == ["地域別の売上を比較してください。"]


def test_analyst_can_manage_only_their_own_preferences(env):
    at = login(start(), env.tokens["alice"])
    at.text_input[0].set_value("要約は結論から書く")
    next(b for b in at.button if b.label == "登録").click().run()
    assert "登録しました" in text(at) and "要約は結論から書く" in text(at)
