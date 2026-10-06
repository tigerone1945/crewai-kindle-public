"""Evaluation Case の `expected` を機械判定する（第2章〜。第18章でランナーに発展させる）。"""
import re

_NUMBER = re.compile(r"\d[\d,]*")


def extract_numbers(text: str) -> set[int]:
    """文中の整数（桁区切りのカンマ付きを含む）を取り出す。小数は整数部と小数部に分かれる点に注意。"""
    return {int(m.replace(",", "")) for m in _NUMBER.findall(text) if m.replace(",", "")}


def check_expected(expected: dict, answer: str, tools_called: list[str], roles_invoked: list[str] | None = None) -> list[str]:
    """期待値との不一致を日本語のメッセージで返す。空リストなら合格。

    roles_invoked は、実際に動いた Role のキー（data_quality など）。Case 07 のように複数 Role の期待があるときに渡す。
    """
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
    any_numbers = expected.get("must_contain_numbers_any_of")
    if any_numbers and not any(n in found for n in any_numbers):
        failures.append(f"期待する数値のどれも含まれていません: {[f'{n:,}' for n in any_numbers]}")
    expected_roles = expected.get("roles_invoked")
    if expected_roles:
        if roles_invoked is None:
            failures.append("roles_invoked の期待があるのに、動いた Role が渡されていません")
        else:
            for role in expected_roles:
                if role not in roles_invoked:
                    failures.append(f"期待する Role が動いていません: {role}（動いた: {sorted(roles_invoked)}）")
    prefix = expected.get("answer_starts_with")
    if prefix and not answer.lstrip().startswith(prefix):
        failures.append(f"回答が「{prefix}」で始まっていません: {answer[:20]!r}")
    for word in expected.get("must_contain_text", []):
        if word not in answer:
            failures.append(f"必須の語がありません: {word}")
    return failures
