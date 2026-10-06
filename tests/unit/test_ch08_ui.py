"""第8章の画面テスト（AppTest）。承認FlowとSQLiteは本物（一時ファイル）、分析サービスとMemoryは偽物。"""
import pytest
from streamlit.testing.v1 import AppTest

import shared.analysis_service as service
import shared.memory_policy as memory_policy
import shared.review_flow as review_flow
import shared.review_requests as review_requests
import shared.tracing as tracing
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch08-human-feedback" / "app.py")
REVIEW = "O0001をレビュー対象として登録してください。"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_policy, "default_preference_store", lambda: memory_policy.PreferenceStore(FakeMemory()))
    monkeypatch.setattr(review_requests, "DEFAULT_DB", tmp_path / "review.db")
    monkeypatch.setattr(review_flow, "DEFAULT_FLOW_DB", tmp_path / "flows.db")
    original_save = tracing.RunTrace.save
    monkeypatch.setattr(tracing.RunTrace, "save", lambda self, directory=tmp_path / "traces": original_save(self, directory))
    calls = []

    def fake_run(question, history=(), preferences=()):
        calls.append(question)
        return SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
                                   metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])

    monkeypatch.setattr(service, "run_analysis", fake_run)
    return type("Env", (), {"store": ReviewStore(tmp_path / "review.db"), "calls": calls})()


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
    at.text_input[1].set_value(approver_id).run()  # [0]=利用者ID, [1]=承認者ID
    return at


def test_review_request_waits_for_approval_and_never_reaches_the_agent(env):
    at = ask(start(), REVIEW)
    assert not at.exception and "承認待ち" in text(at) and "売上 264,600円" in text(at)
    assert env.store.list_requests() == [] and env.calls == []  # 登録されない。Agent・分析にも渡らない
    assert button(at, "承認") and button(at, "却下")


def test_approval_by_another_person_registers_the_request(env):
    at = ask(start(), REVIEW)
    as_approver(at, "approver-001")
    button(at, "承認").click().run()
    assert "登録しました" in text(at)
    [row] = env.store.list_requests()
    assert (row["order_id"], row["requested_by"], row["approved_by"]) == ("O0001", "user-001", "approver-001")
    assert not [b for b in at.button if b.label == "承認"]  # 承認待ちから消える


def test_rejection_registers_nothing(env):
    at = ask(start(), REVIEW)
    button(at, "却下").click().run()
    assert "却下しました" in text(at) and env.store.list_requests() == []


def test_requester_cannot_approve_own_request(env):
    at = ask(start(), REVIEW)
    as_approver(at, "user-001")
    button(at, "承認").click().run()
    assert "本人は承認できません" in text(at) and env.store.list_requests() == []
    assert button(at, "承認")  # 承認待ちのまま


def test_pending_request_survives_an_app_restart(env):
    ask(start(), REVIEW)
    restarted = start()  # 画面（セッション）を作り直す。承認待ちは永続化されている
    assert "承認待ちのレビュー依頼" in text(restarted)
    button(restarted, "承認").click().run()
    assert len(env.store.list_requests()) == 1


def test_stale_screen_cannot_decide_a_request_twice(env):
    at = ask(start(), REVIEW)
    other = start()  # 別の承認者の画面
    button(other, "承認").click().run()
    at.run()  # 古い画面が更新されても、承認待ちは消えていて、二重には登録されない
    assert not at.exception and not [b for b in at.button if b.label == "承認"]
    assert len(env.store.list_requests()) == 1


def test_race_on_resolution_is_reported_not_crashed(env, monkeypatch):
    """同時に操作して、再開の直前に他の承認者が決めた場合（Flow は保留がないと ValueError）。"""
    at = ask(start(), REVIEW)

    def already_resolved(*a, **k):
        raise ValueError("No pending feedback found")

    monkeypatch.setattr(review_flow, "resolve_review", already_resolved)
    button(at, "承認").click()
    at.run()
    as_approver(at, "approver-001")
    button(at, "承認").click().run()
    assert not at.exception and "すでに処理されています" in text(at)


def test_unknown_order_is_reported_without_waiting_for_approval(env):
    at = ask(start(), "O9999をレビュー対象として登録してください。")
    assert "見つかりません" in text(at) and "承認待ちのレビュー依頼" not in text(at)


def test_only_one_order_per_request(env):
    at = ask(start(), "O0001とO0002をレビュー対象に登録して")
    assert "1件ずつ" in text(at) and env.store.pending_approvals() == []


def test_analysis_questions_still_go_to_the_agent(env):
    at = ask(start(), "地域別の売上を比較してください。")
    assert env.calls == ["地域別の売上を比較してください。"] and "承認待ち" not in text(at)


def test_unexpected_failure_does_not_leak_details(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-host:5432 refused")

    monkeypatch.setattr(review_flow, "start_review", boom)
    at = ask(start(), REVIEW)
    assert "エラーが発生しました" in text(at) and "secret-host" not in text(at) and not at.exception


def test_guardrails_still_apply_before_the_review_flow(env):
    at = ask(start(), "API key: sk-abcdef123456 O0001をレビュー対象として登録して")
    assert "秘密情報" in text(at) and env.store.pending_approvals() == []
