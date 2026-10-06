"""機微情報（Secret・個人情報）の検出とマスク（第5章で導入、第6章で共有化）。

BR-10: Secret・個人情報を Log / Trace / Memory へ出さない。
Memory Policy（保存の拒否）と Trace（記録前のマスク）が同じ規則を使う。規則を1か所に置き、判定のずれを防ぐ。
"""
import re

_SECRET_KEYWORD = r"(?:password|passwd|api[ _-]?key|secret|token)"
_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._~+/-]{8,}", re.I)
_SECRET_ASSIGNMENT = re.compile(rf"({_SECRET_KEYWORD}|パスワード)\s*[:=：]\s*\S+", re.I)
_SECRET_MENTION = re.compile(rf"{_SECRET_KEYWORD}\s*[:=]|パスワード", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<![0-9])0\d{1,3}-\d{2,4}-\d{3,4}(?![0-9])")  # 直前が日本語でも検出できるよう \b は使わない


def contains_secret(text: str) -> bool:
    return bool(_SECRET_TOKEN.search(text) or _SECRET_MENTION.search(text))


def contains_personal(text: str) -> bool:
    return bool(_EMAIL.search(text) or _PHONE.search(text))


def redact(text: str, max_length: int = 200) -> str:
    """Secret・メールアドレス・電話番号をマスクし、長すぎる場合は切り詰める。"""
    text = _SECRET_TOKEN.sub("[SECRET]", text)
    text = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[SECRET]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    text = " ".join(text.split())  # 改行や連続した空白を1つにする（Traceを1行で読めるように）
    return text if len(text) <= max_length else text[: max_length - 1] + "…"
