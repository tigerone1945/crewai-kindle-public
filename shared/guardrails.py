"""Guardrail（第7章）: 安全の境界。入力は Crew の前で、出力は Task の中で、決定論的に検査する。

Instructions（Agent への指示）は「お願い」であり、守られる保証がない。Guardrail は「コードで止める」仕組み。
- 入力側（check_question）: Crew・LLM を呼ぶ前に止める。範囲外・秘密情報・個人情報・既知の指示上書きの表現。
- 出力側（OutputGuard）: CrewAI の Task Guardrail。Schema と数値の裏づけを検査し、通らなければ理由を Agent へ返して再生成させる。

限界（第7章 Design Decision）:
- 入力側の範囲外判定は、語彙による粗い関所であり、意図を判定するものではない。
- 指示上書きの検出は既知の表現の一覧であり、言い換えで回避できる。本当の防御は、Tool を目的限定にして、
  Agent に危険な手段を持たせないこと（第2章）と、出力を検査すること。
- Guardrail は Authorization（誰に何を許すか）の代わりにならない（第16章）。
"""
import logging
import time
from typing import Any

from crewai.tasks.task_output import TaskOutput
from pydantic import ValidationError

from shared.sales_validation import SnapshotRepository, find_ungrounded_metrics, find_ungrounded_numbers_in_text
from shared.schemas import SalesAnalysisResult
from shared.sensitive import contains_personal, contains_secret, redact
from shared.tracing import record_app_event

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 500
MAX_FOLLOW_UP_LENGTH = 60  # 会話の続きとして、語彙なしで受け付ける長さの上限

# 売上分析の質問に現れる語。この語を1つも含まない質問は、会話の続きでない限り範囲外とみなす。
DOMAIN_TERMS = (
    "売上", "売り上げ", "受注", "注文", "地域", "カテゴリ", "商品", "顧客", "キャンセル", "件数", "金額",
    "平均", "合計", "割合", "構成比", "ランキング", "レビュー", "担当",
    "関東", "関西", "中部", "東北", "九州", "IT機器", "周辺機器", "オフィス家具", "ストレージ", "法人", "個人",
)

# 既知の「指示の上書き」「設定の聞き出し」の表現。網羅はできないため、これだけに頼らない。
_INJECTION_MARKERS = (
    "指示を無視", "指示をすべて無視", "指示を忘れ", "これまでの指示", "上記の指示", "以前の指示",
    "システムプロンプト", "システム プロンプト", "あなたの設定", "あなたの役割を", "backstory",
    "ignore previous", "ignore all previous", "ignore the above", "system prompt", "disregard",
)


class InputRejected(ValueError):
    """入力が安全の境界を越えた。利用者が直せる問題なので、`message` は利用者へそのまま見せてよい。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _reject(code: str, message: str) -> InputRejected:
    record_app_event("guardrail", "input", "error", rule=code)  # 理由の種類だけを残す。質問の内容は残さない
    return InputRejected(code, message)


def check_question(question: str, has_history: bool = False) -> str:
    """質問が安全の境界の内側なら、整えた文字列を返す。外側なら InputRejected。LLM は呼ばない。"""
    question = question.strip()
    if not question:
        raise _reject("empty", "分析したい内容を入力してください。")
    if len(question) > MAX_QUESTION_LENGTH:
        raise _reject("too_long", f"質問は{MAX_QUESTION_LENGTH}文字以内で入力してください。")
    if contains_secret(question):
        raise _reject("secret", "パスワードやAPIキーなどの秘密情報が含まれているようです。削除してから、もう一度入力してください。")
    if contains_personal(question):
        raise _reject("personal", "メールアドレスや電話番号などの個人情報が含まれているようです。削除してから、もう一度入力してください。")
    lowered = question.lower()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        raise _reject("injection", "この内容には対応できません。売上データについての質問を入力してください。")
    in_domain = any(term in question for term in DOMAIN_TERMS)
    is_follow_up = has_history and len(question) <= MAX_FOLLOW_UP_LENGTH
    if not in_domain and not is_follow_up:
        raise _reject("out_of_scope", "この質問には対応できません。売上・受注データ（地域別・カテゴリ別など）についての質問を入力してください。")
    return question


_EXHAUSTED_PREFIX = "Task failed guardrail validation"  # CrewAI がリトライを使い切ったときに送出する Exception の書き出し（検証済み: Spike s13）


class OutputGuard:
    """Task Guardrail。`Task(guardrail=guard.check, guardrail_max_retries=n)` として渡す。

    CrewAI は渡された Guardrail に inspect.getsource() を呼ぶため、__call__ を持つクラスのインスタンスは渡せない
    （TypeError。Spike s14 の実 Crew で判明。偽物の Crew では検出できない）。関数か束縛メソッドを渡す。

    検査に通らなければ (False, 理由) を返す。CrewAI が理由を Agent へ返し、同じ Task の中で再生成させる（Toolは呼び直さない）。
    1実行ごとに作る（検査の回数と最後の理由を、この実行のために覚えるため）。
    """

    def __init__(self, repo) -> None:
        self._repo = SnapshotRepository(repo)  # 検査のたびに、同じ集計を取り直さない
        self.checks = 0
        self.rejections = 0
        self.last_reason = ""
        self.result: SalesAnalysisResult | None = None  # 検査に通った結果。CrewOutput.pydantic は Guardrail 付きだと空になることがある（Spike s14）

    def check(self, output: TaskOutput) -> tuple[bool, Any]:
        self.checks += 1
        started = time.perf_counter()
        rule, reason, result = self._inspect(output)
        # 検査の時間も Trace に残す。残さないと、「Tool と LLM では説明できない時間」が、原因の分からない遅さになる（Spike s25）
        record_app_event("check", "output_guard", "ok" if reason is None else "rejected",
                         duration_ms=round((time.perf_counter() - started) * 1000), rule=rule if reason else None)
        if reason is None:
            self.result = result
            record_app_event("attempt", "analysis", attempt=self.checks)
            return True, output
        self.rejections += 1
        self.last_reason = reason
        record_app_event("attempt", "analysis", "error", attempt=self.checks, rule=rule, error=redact(reason))
        return False, reason

    def _inspect(self, output: TaskOutput) -> tuple[str, str | None, SalesAnalysisResult | None]:
        # 初回の検査では output.pydantic が空のことがある（Spike s13）。raw から自分で組み立てる。
        result = output.pydantic
        if not isinstance(result, SalesAnalysisResult):
            try:
                result = SalesAnalysisResult.model_validate_json(output.raw)
            except ValidationError as e:
                return "schema", f"出力がSchemaに適合しません: {e.errors()[0]['msg']}。決められた形式で再度回答してください。", None
        problems = find_ungrounded_metrics(result, self._repo) + find_ungrounded_numbers_in_text(result, self._repo)
        if problems:
            return "grounding", (
                "実データに裏づけのない数値があります: " + "; ".join(problems)
                + "。構成比や金額を自分で足したり引いたり丸めたりして書いてはいけません。"
                "Toolの結果に書かれている値（例: 東北 29.8%）をそのまま使うか、数値を使わない表現に書き換えて、再度回答してください。"
            ), None
        text = " ".join([result.summary, *result.insights, *result.recommendations])
        if contains_secret(text) or contains_personal(text):
            return "sensitive", "回答に秘密情報や個人情報のように見える内容が含まれています。取り除いて再度回答してください。", None
        return "ok", None, result

    def is_exhausted(self, error: Exception) -> bool:
        """CrewAI がリトライを使い切って送出した Exception か。Guardrail が実際に却下した場合に限る。"""
        return self.rejections > 0 and str(error).startswith(_EXHAUSTED_PREFIX)
