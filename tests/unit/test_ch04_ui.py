"""Streamlit画面のテスト（AppTestでブラウザなしに実行。分析サービスは差し替えるためLLMを呼ばない）。"""
import json

from streamlit.testing.v1 import AppTest

import shared.analysis_service as service
from shared.schemas import Metric, SalesAnalysisResult
from tests.helpers import ROOT

APP = str(ROOT / "chapters" / "ch04-streamlit-ui" / "app.py")


def result(analysis_type="region_sales"):
    if analysis_type == "unsupported":
        return SalesAnalysisResult(question="q", analysis_type="unsupported", summary="予測はできません",
                                   metrics=[], insights=[], recommendations=[])
    return SalesAnalysisResult(
        question="q", analysis_type=analysis_type, summary="東北が最も高い売上です",
        metrics=[Metric(label="東北の売上", value=8439765, unit="円"), Metric(label="東北の構成比", value=29.8, unit="%")],
        insights=["東北が1位", "関西が僅差で2位"], recommendations=["九州の要因を確認"],
    )


def run(monkeypatch, question="地域別の売上を比較してください。", fake=None):
    if fake is not None:
        monkeypatch.setattr(service, "run_analysis", fake)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_area(key="question").set_value(question)
    at.button[-1].click().run()  # 最後のボタンはフォームの「分析する」
    return at


def texts(at):
    return " ".join(e.value for e in [*at.markdown, *at.warning, *at.error, *at.info, *at.success])


def test_initial_render_has_title_examples_and_form():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "AI売上分析アシスタント"
    assert len([b for b in at.button if b.label.endswith("ください。")]) == 3
    assert at.button[-1].label == "分析する"


def test_example_button_fills_the_question():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button(key="example_2").click().run()
    assert at.text_area(key="question").value == "地域別の売上を比較してください。"


def test_empty_question_is_rejected_without_calling_service(monkeypatch):
    called = []
    at = run(monkeypatch, question="   ", fake=lambda q: called.append(q))
    assert "入力してください" in texts(at) and not called


def test_too_long_question_is_rejected(monkeypatch):
    called = []
    at = run(monkeypatch, question="あ" * 501, fake=lambda q: called.append(q))
    assert "500文字以内" in texts(at) and not called


def test_success_shows_summary_metrics_insights_and_json(monkeypatch):
    at = run(monkeypatch, fake=lambda q: result())
    assert not at.exception
    assert "東北が最も高い売上です" in texts(at)
    table = at.dataframe[0].value
    assert table["値"].tolist() == ["8,439,765", "29.8"] and table["単位"].tolist() == ["円", "%"]
    assert "東北が1位" in texts(at) and "九州の要因を確認" in texts(at)
    assert json.loads(at.json[0].value)["analysis_type"] == "region_sales"


def test_unsupported_shows_notice_not_metrics(monkeypatch):
    at = run(monkeypatch, fake=lambda q: result("unsupported"))
    assert "対応できません" in texts(at) and len(at.dataframe) == 0


def test_validation_failure_shows_friendly_warning(monkeypatch):
    def reject(q):
        raise service.AnalysisError("3回試しましたが検証を通りませんでした: 文中の 60% は実データから…")

    at = run(monkeypatch, fake=reject)
    assert "質問の表現を変えて" in texts(at)
    assert "60%" not in texts(at) and not at.exception  # 検証の内部メッセージは画面に出さない


def test_unexpected_error_does_not_leak_details(monkeypatch):
    def boom(q):
        raise ConnectionError("secret-host:5432 refused")

    at = run(monkeypatch, fake=boom)
    assert "エラーが発生しました" in texts(at)
    assert "secret-host" not in texts(at) and not at.exception
