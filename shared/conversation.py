"""会話の文脈（第5章）。同じ会話の中だけで使う、揮発する短期の文脈。

保存先は呼び出し側（画面のセッション）で、ファイルやDBへは書かない。
直前の質問と分析種別・要約だけを持ち、数値の正本にはしない。数値は毎回Toolで取得し直す。
"""
from dataclasses import dataclass

from shared.schemas import SalesAnalysisResult

MAX_TURNS = 5


@dataclass(frozen=True)
class Turn:
    question: str
    analysis_type: str
    summary: str

    @classmethod
    def from_result(cls, question: str, result: SalesAnalysisResult) -> "Turn":
        return cls(question=question, analysis_type=result.analysis_type, summary=result.summary)


def build_history_text(history: list[Turn] | tuple[Turn, ...], max_turns: int = MAX_TURNS) -> str:
    recent = list(history)[-max_turns:]
    return "\n".join(
        f"{i}. 質問: {t.question} / 分析種別: {t.analysis_type} / 要約: {t.summary}"
        for i, t in enumerate(recent, 1)
    )
