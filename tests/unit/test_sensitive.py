import pytest

from shared.sensitive import contains_personal, contains_secret, redact


@pytest.mark.parametrize("text", ["sk-abcdef123456", "password: hunter2", "API key = abc", "パスワードは変更しました", "Bearer abcdefgh12345"])
def test_secrets_are_detected(text):
    assert contains_secret(text)


@pytest.mark.parametrize("text", ["a@example.com", "連絡先03-1234-5678", "090-1234-5678"])
def test_personal_information_is_detected(text):
    assert contains_personal(text)


@pytest.mark.parametrize("text", ["地域別の売上を比較する", "注文O0001は2026-01-15", "合計28298695円"])
def test_ordinary_text_is_not_flagged(text):
    assert not contains_secret(text) and not contains_personal(text)


def test_redact_masks_values_but_keeps_the_context_for_debugging():
    out = redact("auth failed: password: hunter2, key sk-abcdef123456, mail a@example.com, tel 03-1234-5678")
    assert "hunter2" not in out and "sk-abcdef123456" not in out and "a@example.com" not in out and "03-1234-5678" not in out
    assert out.startswith("auth failed:")


def test_redact_truncates_and_flattens_whitespace():
    out = redact("line1\n\n   line2 " + "x" * 500, max_length=50)
    assert len(out) == 50 and out.endswith("…") and "\n" not in out
