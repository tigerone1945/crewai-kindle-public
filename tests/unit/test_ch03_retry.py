"""run_analysis の再試行ロジック（LLMを呼ばずに、_analyze_once を差し替えて検証する）。"""
import pytest

from tests.helpers import load_chapter_module

main = load_chapter_module("ch03-structured-output", "main.py")
OK = object()


def test_returns_immediately_when_first_attempt_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_analyze_once", lambda p: calls.append(p) or OK)
    assert main.run_analysis("q") is OK
    assert calls == ["q"]


def test_retries_with_rejection_reason_then_succeeds(monkeypatch):
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        if len(prompts) < 3:
            raise main.AnalysisError("文中の 60% は実データから得られる値ではありません")
        return OK

    monkeypatch.setattr(main, "_analyze_once", fake)
    assert main.run_analysis("q") is OK
    assert len(prompts) == 3
    assert prompts[0] == "q" and "却下" in prompts[1] and "60%" in prompts[1]


def test_raises_after_max_attempts(monkeypatch):
    calls = []

    def always_fail(prompt):
        calls.append(prompt)
        raise main.AnalysisError("NG")

    monkeypatch.setattr(main, "_analyze_once", always_fail)
    with pytest.raises(main.AnalysisError, match="3回試しました"):
        main.run_analysis("q")
    assert len(calls) == 3


def test_unexpected_exceptions_are_not_swallowed(monkeypatch):
    def boom(prompt):
        raise ConnectionError("network")

    monkeypatch.setattr(main, "_analyze_once", boom)
    with pytest.raises(ConnectionError):
        main.run_analysis("q")
