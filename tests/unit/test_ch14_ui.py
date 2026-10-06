"""第14章の画面テスト（AppTest）。画面は、本物の API（FastAPI の TestClient）だけを呼ぶ。
API の中の Flow・承認 Flow・SQLite は本物（一時ファイル）。LLM を呼ぶ分析サービスと Memory は偽物。"""
import ast

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import shared.analysis_flow as analysis_flow
import shared.analysis_service as service
import shared.api as api
import shared.api_client as api_client
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch14-fastapi-service" / "ui.py")
REVIEW = "O0001をレビュー対象として登録してください。"


def result(analysis_type="region_sales"):
    return SalesAnalysisResult(question="q", analysis_type=analysis_type, summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls, multi = [], []

    def fake_run(question, history=(), preferences=()):
        calls.append(dict(question=question, history=history, preferences=preferences))
        return result()

    def fake_multi(question):
        multi.append(question)
        return result("integrated_analysis")

    monkeypatch.setattr(service, "run_analysis", fake_run)
    monkeypatch.setattr(service, "run_multi_role_analysis", fake_multi)
    monkeypatch.setattr(analysis_flow, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    memory = FakeMemory()
    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "review.db"), flow_db=tmp_path / "flows.db",
                            preference_store_factory=lambda: PreferenceStore(memory), trace_dir=tmp_path / "traces")
    http = TestClient(api.create_app(deps), base_url="http://testserver", raise_server_exceptions=False)
    # 画面が作るクライアントを、この API（通信なし）へつなぐ
    real_client = api_client.AnalysisApiClient
    monkeypatch.setattr(api_client, "AnalysisApiClient", lambda *a, **k: real_client(http=http))
    return type("Env", (), {"deps": deps, "calls": calls, "multi": multi, "http": http, "memory": memory})()


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    return at


def text(at):
    return " ".join(e.value for e in [*at.markdown, *at.subheader, *at.warning, *at.error, *at.info, *at.success, *at.caption])


def button(at, label):
    return next(b for b in at.button if b.label == label)


def as_approver(at, approver_id):
    at.text_input[1].set_value(approver_id).run()
    return at


def test_question_goes_through_the_api_and_shows_the_result_route_and_run_id(env):
    at = ask(start(), "地域別の売上を比較してください。")
    assert not at.exception and "東北が最も高い売上です" in text(at)
    assert "経路: 単一の Agent で分析" in text(at) and "run_id=" in text(at)
    assert len(env.calls) == 1


def test_history_is_sent_to_the_api_for_follow_up_questions(env):
    at = ask(start(), "地域別の売上を比較してください。")
    ask(at, "では一番高かった地域は？")
    assert len(env.calls) == 2 and env.calls[1]["history"][0].summary == "東北が最も高い売上です"


def test_quality_question_uses_the_three_roles(env):
    at = ask(start(), "データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
    assert len(env.multi) == 1 and "3つの Role" in text(at)


def test_input_rejection_is_shown_as_a_warning_and_no_service_runs(env):
    at = ask(start(), "Pythonでゲームを作ってください。")
    assert "対応できません" in text(at) and env.calls == [] and env.multi == []


def test_secret_in_the_question_is_rejected_without_being_echoed_by_the_api(env):
    at = ask(start(), "API key: sk-abcdef123456 売上を教えて")
    assert "秘密情報" in text(at) and env.calls == []


def test_review_request_waits_for_approval_and_never_reaches_the_agent(env):
    at = ask(start(), REVIEW)
    assert "承認待ち" in text(at) and "売上 264,600円" in text(at)
    assert env.deps.review_store.list_requests() == [] and env.calls == []
    assert button(at, "承認") and button(at, "却下")


def test_approval_by_another_person_registers_the_request(env):
    at = ask(start(), REVIEW)
    as_approver(at, "approver-001")
    button(at, "承認").click().run()
    assert "登録しました" in text(at)
    [row] = env.deps.review_store.list_requests()
    assert (row["order_id"], row["requested_by"], row["approved_by"]) == ("O0001", "user-001", "approver-001")


def test_requester_cannot_approve_own_request(env):
    at = ask(start(), REVIEW)
    as_approver(at, "user-001")
    button(at, "承認").click().run()
    assert "本人は承認できません" in text(at) and env.deps.review_store.list_requests() == []
    assert button(at, "承認")


def test_pending_request_survives_a_ui_restart(env):
    ask(start(), REVIEW)
    restarted = start()  # UI のプロセスを作り直す。承認待ちは、サービス側にある
    assert "承認待ちのレビュー依頼" in text(restarted)
    button(restarted, "承認").click().run()
    assert len(env.deps.review_store.list_requests()) == 1


def test_stale_screen_cannot_decide_a_request_twice(env):
    at = ask(start(), REVIEW)
    other = start()
    button(other, "承認").click().run()
    at.run()
    assert not at.exception and not [b for b in at.button if b.label == "承認"]
    assert len(env.deps.review_store.list_requests()) == 1


def test_race_on_resolution_is_reported_not_crashed(env):
    """承認待ちを表示してから、ボタンを押すまでの間に、別の承認者が処理した場合（サービスは 409 を返す）。"""
    at = ask(start(), REVIEW)

    def already_resolved(*args, **kwargs):
        raise ValueError("No pending feedback found")

    env.deps.resolve_review = already_resolved
    as_approver(at, "approver-001")
    button(at, "承認").click().run()
    assert not at.exception and "すでに処理されています" in text(at)


def test_preferences_are_managed_through_the_api_per_actor(env):
    at = start()
    at.text_input[2].set_value("要約は結論から書く")  # [0]=利用者ID, [1]=承認者ID, [2]=好み
    at.button[0].click().run()  # フォームの「登録」
    assert "登録しました" in text(at) and "要約は結論から書く" in text(at)
    ask(at, "地域別の売上を比較してください。")
    assert env.calls[-1]["preferences"] == ["要約は結論から書く"]  # API の中で、好みが取得される


def test_preference_policy_violation_is_shown(env):
    at = start()
    at.text_input[2].set_value("今月の売上は1,234,567円だった")
    at.button[0].click().run()
    assert "登録できません" in text(at) and env.memory.records == []


def test_api_failure_does_not_leak_details_and_does_not_crash(env, monkeypatch):
    def down(question, history=(), preferences=()):
        raise ConnectionError("secret-host:5432 refused")

    monkeypatch.setattr(service, "run_analysis", down)
    at = ask(start(), "地域別の売上を比較してください。")
    assert "担当者へ連絡してください" in text(at) and "secret-host" not in text(at) and not at.exception


def test_unreachable_api_is_reported_without_crashing(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("secret-host:8000 refused")

    http = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x")
    real = api_client.AnalysisApiClient
    monkeypatch.setattr(api_client, "AnalysisApiClient", lambda *a, **k: real(http=http))
    at = start()
    assert not at.exception and "接続できませんでした" in text(at) and "secret-host" not in text(at)
    ask(at, "地域別の売上を比較してください。")
    assert "接続できませんでした" in text(at) and not at.exception


def test_the_ui_does_not_import_the_analysis_side():
    """UI と API の分離: 画面のコードは、CrewAI・Flow・DB・Memory を直接 import しない。"""
    tree = ast.parse(open(APP, encoding="utf-8").read())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in modules if m.split(".")[0] == "crewai"}
    forbidden = ("shared.analysis_service", "shared.analysis_flow", "shared.review_flow", "shared.review_requests", "shared.memory_policy",
                 "shared.sales_db", "shared.tracing", "shared.roles", "shared.guardrails", "shared.ui_components")
    assert not [m for m in modules if m in forbidden], modules
