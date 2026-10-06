"""LLM-as-a-Judge（第18章）: Business Usefulness（業務として、役に立つか）を、LLM に採点させる。**参考値であり、合否の基準にしない。**

Code で決められるもの（数値・Tool・順序）は、grading.py で決定的に採点する。意味・有用性は、Code では決められないので、LLM に任せる。
ただし、LLM の採点は、ぶれる。そこで、次の2つを、使う前に測る（Spike s26）。
- 安定性: 同じ回答を、同じ基準で、何度採点しても、同じ結果になるか
- 識別力: 劣化させた回答（一般的すぎる提案・結果と矛盾する洞察）を、劣化していない回答より低く採点するか
採点の基準は、3つの問い（はい / いいえ）。点数ではなく、具体的な問いにして、判断をぶらさない。
"""
import json
import re
from dataclasses import dataclass

from shared.schemas import SalesAnalysisResult

CRITERIA = {
    "actionable": "recommendations は、読んだ人が、次に何をするかを決められる具体的な内容か（「データを確認する」のような、どの分析にも言える一般論ではない）",
    "specific": "insights と summary は、metrics の具体的な値や項目（地域名・カテゴリ名など）に基づいて書かれているか",
    "consistent": "summary・insights・recommendations は、metrics の内容と矛盾していないか（矛盾がなければ「はい」）",
}

PROMPT = """あなたは、売上分析の回答を評価する審査員です。次の分析結果を、3つの問いに「はい」か「いいえ」で答えてください。

質問: {question}

分析結果:
- summary: {summary}
- metrics: {metrics}
- insights: {insights}
- recommendations: {recommendations}

問い:
{criteria}

次の JSON だけを出力してください。説明は不要です。
{{"actionable": true/false, "specific": true/false, "consistent": true/false}}"""


@dataclass(frozen=True)
class Judgement:
    actionable: bool
    specific: bool
    consistent: bool

    @property
    def score(self) -> int:
        return int(self.actionable) + int(self.specific) + int(self.consistent)


def build_prompt(question: str, result: SalesAnalysisResult) -> str:
    return PROMPT.format(
        question=question, summary=result.summary,
        metrics="; ".join(f"{m.label}={m.value:,.1f}{m.unit}" for m in result.metrics),
        insights=" / ".join(result.insights), recommendations=" / ".join(result.recommendations),
        criteria="\n".join(f"- {k}: {v}" for k, v in CRITERIA.items()))


def parse_judgement(text: str) -> Judgement:
    """LLM の出力から、JSON を取り出す。形式が崩れていたら、例外（採点できなかったことを、成功に見せない）。"""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("審査員の出力に JSON がありません")
    data = json.loads(match.group(0))
    if set(data) != set(CRITERIA) or not all(isinstance(v, bool) for v in data.values()):
        raise ValueError(f"審査員の出力の形式が正しくありません: {sorted(data)}")
    return Judgement(**data)


def judge(question: str, result: SalesAnalysisResult, llm=None) -> Judgement:
    if llm is None:
        from shared.sales_data import get_llm
        llm = get_llm()
    return parse_judgement(llm.call([{"role": "user", "content": build_prompt(question, result)}]))
