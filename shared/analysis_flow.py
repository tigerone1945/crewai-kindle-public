"""分析の Flow（第13章）。Crew = Agency、Flow = Deterministic Backbone。

質問を受けてから結果を返すまでの「流れ」を、LLM ではなくコードで決める。Agent（Crew）が担うのは、各ステップの中の分析だけ。

  receive ─ 入力を検査 ─ route ─ rejected ───────────────────────────→ （終了。LLM を呼ばない）
                              ├ review_request → request_review（承認 Flow へ。ここで人の判断を待つ。第8章）
                              ├ single ──────┐
                              └ integrated ──┴→ run_analysis ─ evaluate ─ done
                                                      ↑            ├ retry（通信などの一時的な失敗）──┘
                                                      └────────────┴ fallback → escalate（人へ引き継ぐ）

決めること（Flow が固定する）: 入力を受け付けるか / どの経路へ進むか / 何を再試行するか / いつ人に引き継ぐか / 承認が要る処理。
任せること（Crew が担う）: 経路の中での、Tool の選択と、分析の文章。

再試行は、失敗の種類で分ける。
- 出力の検査（Guardrail）を使い切って失敗: 再試行しない。サービスの中で、却下の理由を渡して作り直し済みで、同じ入力で繰り返しても通らない（Spike s17, s21）。
- 通信などの一時的な失敗: 間隔をあけて、最大 MAX_TRANSIENT_RETRIES 回まで再試行する。
どちらも、使い切ったら、回答を作らずに人へ引き継ぐ（エスカレーション）。数値を作って返すことはしない。
"""
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from crewai.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

import shared.analysis_service as service
from shared.conversation import Turn
from shared.guardrails import InputRejected, check_question
from shared.review_flow import DEFAULT_FLOW_DB, extract_review_order_ids, start_review
from shared.review_requests import ReviewStore
from shared.schemas import SalesAnalysisResult
from shared.tracing import record_app_event

logger = logging.getLogger("analysis_flow")

MAX_TRANSIENT_RETRIES = 2  # 一時的な失敗の再試行（初回を含めず）
RETRY_BACKOFF_SECONDS = (1.0, 3.0)  # 再試行の前の待ち時間。回を重ねるほど長くする
INTEGRATED_WORDS = ("品質",)  # この語を含む質問は、データ品質を含む統合分析（3つの Role。第11章）へ進める

ESCALATION_MESSAGES = {
    "guardrail": "正確な回答を生成できませんでした。質問の表現を変えて、もう一度お試しください。解決しない場合は、担当者へ連絡してください。",
    "transient": "ただいま分析サービスに接続できませんでした。時間をおいて、もう一度お試しください。解決しない場合は、担当者へ連絡してください。",
}


def choose_route(question: str) -> str:
    """検査を通った質問が、どの経路へ進むか。LLM は呼ばない。誤って状態を変える経路へ進まないよう、規則は固定する。

    限界: 語による粗い振り分けであり、意図を理解するものではない。言い換えは、別の経路へ進みうる。
    承認が要る経路（review_request）は、より危険な誤りが「登録しない」方向になるよう、依頼の語と注文IDの両方を要求する。
    """
    if extract_review_order_ids(question):
        return "review_request"
    if any(word in question for word in INTEGRATED_WORDS):
        return "integrated"
    return "single"


class AnalysisState(BaseModel):
    question: str = ""
    route: str = ""
    attempts: int = 0
    outcome: str = ""  # analysis / rejected / review_pending / review_invalid / escalated
    message: str = ""  # 利用者へ見せてよい文
    reason: str = ""  # rejected のとき: 入力検査の理由コード
    error_kind: str = ""  # run_analysis の直近の失敗: "" / guardrail / transient
    result: SalesAnalysisResult | None = None
    review_flow_id: str = ""
    review_order_id: str = ""


class AnalysisFlow(Flow[AnalysisState]):
    def __init__(self, *, actor_id: str, review_store: ReviewStore | None = None, flow_db=DEFAULT_FLOW_DB,
                 history: Sequence[Turn] = (), preferences_for: Callable[[str], list[str]] | None = None,
                 sleep: Callable[[float], None] = time.sleep, **kwargs) -> None:
        super().__init__(**kwargs)
        self._actor_id = actor_id
        self._review_store = review_store
        self._flow_db = flow_db
        self._history = tuple(history)
        self._preferences_for = preferences_for
        self._sleep = sleep

    @start()
    def receive(self):
        """入力の検査。LLM を呼ぶ前に止める（第7章）。"""
        try:
            self.state.question = check_question(self.state.question, has_history=bool(self._history))
        except InputRejected as e:
            self.state.outcome, self.state.message, self.state.reason = "rejected", e.message, e.code

    @router(receive)
    def route(self):
        route = "rejected" if self.state.outcome == "rejected" else choose_route(self.state.question)
        self.state.route = route
        record_app_event("flow", "route_chosen", route=route)  # 経路の選択を、Trace から辿れるようにする（質問の内容は残さない）
        return route

    @listen("review_request")
    def request_review(self):
        """状態を変える依頼は、Agent に渡さない。承認 Flow へ進め、人の承認を待つ（第8章。BR-05, BR-06）。"""
        order_ids = extract_review_order_ids(self.state.question)
        if len(order_ids) > 1:
            self.state.outcome, self.state.message = "review_invalid", "レビュー依頼は、一度に1件ずつ登録してください。"
            return
        ticket = start_review(order_ids[0], self._actor_id, self._review_store, self._flow_db)
        if ticket.status == "invalid":
            self.state.outcome, self.state.message = "review_invalid", ticket.message
            return
        self.state.outcome = "review_pending"
        self.state.review_flow_id, self.state.review_order_id = ticket.flow_id, ticket.order_id
        self.state.message = f"注文 {ticket.order_id} のレビュー依頼を、承認待ちにしました。承認されるまで、登録はされません。"

    @listen(or_("single", "integrated", "retry"))
    def run_analysis(self):
        """Crew を呼ぶ。失敗は例外のまま流さず、State の種類に変えて、次の判断（evaluate）をコードで行う。"""
        s = self.state
        s.attempts += 1
        if s.attempts > 1:
            self._sleep(RETRY_BACKOFF_SECONDS[min(s.attempts - 2, len(RETRY_BACKOFF_SECONDS) - 1)])
        try:
            if s.route == "integrated":
                s.result = service.run_multi_role_analysis(s.question)
            else:
                preferences = self._preferences_for(s.question) if self._preferences_for else []
                s.result = service.run_analysis(s.question, history=self._history, preferences=preferences)
        except service.AnalysisError:
            logger.warning("analysis rejected by output guardrail route=%s", s.route)
            s.error_kind = "guardrail"
        except Exception:
            logger.exception("analysis step failed route=%s attempt=%s", s.route, s.attempts)
            s.error_kind = "transient"
        else:
            s.error_kind, s.outcome = "", "analysis"

    @router(run_analysis)
    def evaluate(self):
        kind = self.state.error_kind
        if not kind:
            return "done"
        if kind == "transient" and self.state.attempts <= MAX_TRANSIENT_RETRIES:
            return "retry"
        return "fallback"  # guardrail は再試行しない（サービスの中で作り直し済み）。一時的な失敗も、使い切ったらここ

    @listen("fallback")
    def escalate(self):
        self.state.outcome = "escalated"
        self.state.message = ESCALATION_MESSAGES[self.state.error_kind]


@dataclass(frozen=True)
class FlowOutcome:
    """画面や API が受け取る結果。Flow の内部（State）を、呼び出し側に見せない。"""

    kind: str  # analysis / rejected / review_pending / review_invalid / escalated
    route: str
    result: SalesAnalysisResult | None = None
    message: str = ""
    attempts: int = 0
    review_flow_id: str = ""


def run_question(question: str, actor_id: str, *, review_store: ReviewStore, flow_db=DEFAULT_FLOW_DB,
                 history: Sequence[Turn] = (), preferences_for: Callable[[str], list[str]] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> FlowOutcome:
    """質問を Flow で処理する。呼び出し側は、結果の kind だけで、画面の出し分けができる。"""
    flow = AnalysisFlow(actor_id=actor_id, review_store=review_store, flow_db=flow_db, history=history,
                        preferences_for=preferences_for, sleep=sleep)
    flow.kickoff(inputs={"question": question})
    s = flow.state
    return FlowOutcome(s.outcome, s.route, s.result, s.message, s.attempts, s.review_flow_id)
