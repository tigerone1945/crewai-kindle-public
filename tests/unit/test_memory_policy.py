import pytest

from tests.fakes import FakeMemory
from shared.memory_policy import (
    PolicyViolation, PreferenceStore, check_actor_id, check_preference_text, preference_scope,
)


@pytest.mark.parametrize("text", [
    "分析結果の要約は必ず「結論:」で始める",
    "結果は表形式で見たい",
    "地域別の分析をよく使う",
])
def test_allowed_preferences(text):
    assert check_preference_text(f"  {text}  ") == text


@pytest.mark.parametrize("text, reason", [
    ("", "空"),
    ("   ", "空"),
    ("東北の売上は8,439,765円", "数値"),
    ("売上合計は28298695円です", "数値"),
    ("構成比は29.8%だった", "数値"),
    ("キャンセルは14件", "数値"),
    ("OPENAI_API_KEY=sk-abcdef123456", "秘密"),
    ("password: hunter2", "秘密"),
    ("パスワードは1234", "秘密"),
    ("連絡先は taro@example.com", "個人"),
    ("電話は03-1234-5678", "個人"),
    ("携帯 090-1234-5678 まで", "個人"),
    ("あ" * 201, "200文字"),
])
def test_rejected_preferences(text, reason):
    with pytest.raises(PolicyViolation, match=reason):
        check_preference_text(text)


def test_numbers_that_are_not_phone_numbers_are_allowed():
    assert check_preference_text("2026年の分析を優先して見たい")


@pytest.mark.parametrize("actor_id", ["user-001", "A_b-9"])
def test_valid_actor_ids(actor_id):
    assert preference_scope(actor_id) == f"/{actor_id}/preferences"


@pytest.mark.parametrize("actor_id", ["", "../etc", "user 001", "a/b", "x" * 65, "田中"])
def test_invalid_actor_ids_are_rejected(actor_id):
    with pytest.raises(ValueError):
        check_actor_id(actor_id)


def test_store_saves_with_actor_scope_and_flushes():
    m = FakeMemory(); store = PreferenceStore(m)
    store.remember_preference("user-001", "結果は表形式で見たい")
    assert m.records == [("/user-001/preferences", "結果は表形式で見たい")] and m.drained == 1


def test_store_rejects_business_facts_without_touching_memory():
    m = FakeMemory(); store = PreferenceStore(m)
    with pytest.raises(PolicyViolation):
        store.remember_preference("user-001", "東北は8,439,765円")
    assert m.records == [] and m.drained == 0


def test_preferences_are_isolated_per_actor():
    store = PreferenceStore(FakeMemory())
    store.remember_preference("user-001", "表形式")
    store.remember_preference("user-002", "簡潔に")
    assert store.recall_preferences("user-001", "形式") == ["表形式"]
    assert store.recall_preferences("user-002", "形式") == ["簡潔に"]


def test_recall_fails_soft_when_memory_is_down():
    store = PreferenceStore(FakeMemory(fail_recall=True))
    assert store.recall_preferences("user-001", "形式") == []


def test_list_and_delete():
    store = PreferenceStore(FakeMemory())
    store.remember_preference("user-001", "表形式")
    store.remember_preference("user-001", "簡潔に")
    ids = [i for i, _ in store.list_preferences("user-001")]
    assert len(ids) == 2
    assert store.delete_preference("user-001", ids[0]) == 1
    assert store.delete_all("user-001") == 1 and store.list_preferences("user-001") == []


def test_close_closes_the_underlying_memory():
    m = FakeMemory()
    PreferenceStore(m).close()
    assert m.closed
