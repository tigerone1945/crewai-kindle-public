"""Evaluation Case の `expected` を機械判定する（第2章〜。第18章でランナーに発展させる）。"""
import re

_NUMBER = re.compile(r"\d[\d,]*")


def extract_numbers(text: str) -> set[int]:
    """文中の整数（桁区切りのカンマ付きを含む）を取り出す。小数は整数部と小数部に分かれる点に注意。"""
    return {int(m.replace(",", "")) for m in _NUMBER.findall(text) if m.replace(",", "")}


def check_expected(expected: dict, answer: str, tools_called: list[str]) -> list[str]:
    """期待値との不一致を日本語のメッセージで返す。空リストなら合格。"""
    failures: list[str] = []
    found = extract_numbers(answer)

    any_of = expected.get("tools_any_of")
    if any_of and not set(any_of) & set(tools_called):
        failures.append(f"期待するToolが呼ばれていません: {any_of}（呼ばれた: {tools_called}）")
    for n in expected.get("must_contain_numbers", []):
        if n not in found:
            failures.append(f"必須の数値がありません: {n:,}")
    for n in expected.get("must_not_contain_numbers", []):
        if n in found:
            failures.append(f"含まれてはならない数値があります: {n:,}")
    prefix = expected.get("answer_starts_with")
    if prefix and not answer.lstrip().startswith(prefix):
        failures.append(f"回答が「{prefix}」で始まっていません: {answer[:20]!r}")
    for word in expected.get("must_contain_text", []):
        if word not in answer:
            failures.append(f"必須の語がありません: {word}")
    return failures
