from shared.analysis_service import compose_prompt
from shared.conversation import Turn, build_history_text
from shared.schemas import Metric, SalesAnalysisResult


def turn(i):
    return Turn(question=f"質問{i}", analysis_type="region_sales", summary=f"要約{i}")


def test_history_keeps_only_recent_turns():
    text = build_history_text([turn(i) for i in range(1, 9)], max_turns=5)
    assert "質問3" not in text and "質問4" in text and "質問8" in text
    assert len(text.splitlines()) == 5


def test_turn_from_result_drops_metrics():
    r = SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が1位",
                            metrics=[Metric(label="東北", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])
    t = Turn.from_result("地域別は？", r)
    assert (t.question, t.analysis_type, t.summary) == ("地域別は？", "region_sales", "東北が1位")
    assert "8439765" not in repr(t)  # 数値(metrics)は文脈に持たない


def test_prompt_is_just_the_question_without_context():
    assert compose_prompt("地域別は？") == "地域別は？"


def test_prompt_includes_preferences_and_history_and_marks_them_as_reference_only():
    p = compose_prompt("では一番高い地域は？", history=[turn(1)], preferences=["結論から書く"])
    assert "- 結論から書く" in p and "質問1" in p and p.rstrip().endswith("では一番高い地域は？")
    assert "数値の根拠にはしない" in p and "Toolで取得し直す" in p
