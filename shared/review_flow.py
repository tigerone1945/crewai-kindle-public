"""レビュー依頼の承認 Flow（第8章）。Crew = Agency、Flow = Deterministic Backbone。

Agent に状態を変える Tool は渡さない。登録は、この Flow が、人の承認を得たあとにだけ実行する（BR-05, BR-06）。

  prepare → gate ─ invalid ───────────────────────→ fail
                  └ needs_approval → request_approval（ここで一時停止して人に判断を戻す）
                                        → decide ─ approved → execute
                                                  └ rejected → cancel

承認の判断は LLM に任せない（Spike s15）:
- `@human_feedback(emit=[...])` は、人の返答を LLM が分類するため、「approved」と正確に入力しても LLM が呼ばれ、
  条件つきの返答（"approved but only if..."）まで承認として実行された。
- そこで emit を使わず、返答を `@router` で完全一致だけ承認と判定する。それ以外はすべて却下（拒否がDefault）。
一時停止と再開は、別の Flow インスタンス（別プロセス）でもできる。状態は永続化される（Flow.from_pending）。
"""
import re
from dataclasses import dataclass
from typing import Literal

from crewai.flow import Flow, HumanFeedbackPending, HumanFeedbackProvider, human_feedback, listen, router, start
from crewai.flow.persistence import SQLiteFlowPersistence
from pydantic import BaseModel, Field

from shared.memory_policy import check_actor_id
from shared.review_requests import (DuplicateRequest, InvalidReviewTarget, ReviewStore, validate_review_target)
from shared.sales_data import ROOT

DEFAULT_FLOW_DB = ROOT / "data" / "flows.db"
APPROVE, REJECT = "approve", "reject"  # 承認として受け付ける唯一の文字列


_ORDER_ID_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])O\d{4}(?![0-9])")  # 直前・直後が日本語でも検出できるよう \b は使わない
_REVIEW_REQUEST_WORDS = ("登録", "依頼", "追加", "対象にして")


def extract_review_order_ids(question: str) -> list[str]:
    """「O0001をレビュー対象として登録してください」のような依頼から注文IDを取り出す。依頼でなければ空。

    状態を変える依頼かどうかは、LLM ではなく、決定論的な規則で判定する（誤って登録に進まないため）。
    本格的な振り分けは第13章で Flow の Router にする。
    """
    if "レビュー" not in question or not any(w in question for w in _REVIEW_REQUEST_WORDS):
        return []
    return list(dict.fromkeys(_ORDER_ID_IN_TEXT.findall(question)))


class SelfApprovalError(PermissionError):
    """依頼した本人は、その依頼を承認できない（職務の分離）。"""


class ReviewState(BaseModel):
    order_id: str = ""
    requested_by: str = ""
    approved_by: str = ""
    facts: dict = Field(default_factory=dict)
    error_code: str = ""
    result: str = ""  # registered / cancelled / duplicate / invalid:<code> / not_eligible


class _PauseProvider(HumanFeedbackProvider):
    """人の入力をその場で待たず、Flow を一時停止する（再開は Flow.from_pending(...).resume(...)）。"""

    def request_feedback(self, context, flow):
        raise HumanFeedbackPending(context=context)


class ReviewFlow(Flow[ReviewState]):
    def __init__(self, store: ReviewStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self._store = store

    @start()
    def prepare(self):
        try:
            self.state.facts = validate_review_target(self.state.order_id)
        except InvalidReviewTarget as e:
            self.state.error_code = e.code
            self._store.audit("invalid", self.state.id, self.state.order_id, self.state.requested_by, e.code)

    @router(prepare)
    def gate(self):
        return "invalid" if self.state.error_code else "needs_approval"

    @listen("needs_approval")
    @human_feedback(message="このレビュー依頼を登録してよいですか？", provider=_PauseProvider())
    def request_approval(self):
        self._store.audit("proposed", self.state.id, self.state.order_id, self.state.requested_by)
        return self.state.facts

    @router(request_approval)
    def decide(self):
        feedback = self.last_human_feedback
        return "approved" if feedback is not None and feedback.feedback.strip() == APPROVE else "rejected"

    @listen("approved")
    def execute(self):
        s = self.state
        self._store.audit("approved", s.id, s.order_id, s.approved_by)
        try:
            validate_review_target(s.order_id)  # 承認は提案時点の判断。実行の直前に、前提が変わっていないかを確かめ直す
            self._store.create_request(s.order_id, s.requested_by, s.approved_by, s.id)
        except InvalidReviewTarget as e:
            s.result = "not_eligible"
            self._store.audit("execution_failed", s.id, s.order_id, s.approved_by, e.code)
        except DuplicateRequest:
            s.result = "duplicate"
            self._store.audit("execution_failed", s.id, s.order_id, s.approved_by, "duplicate")
        else:
            s.result = "registered"
            self._store.audit("executed", s.id, s.order_id, s.approved_by)

    @listen("rejected")
    def cancel(self):
        s = self.state
        self.state.result = "cancelled"
        self._store.audit("rejected", s.id, s.order_id, s.approved_by or "unknown")

    @listen("invalid")
    def fail(self):
        self.state.result = f"invalid:{self.state.error_code}"


@dataclass(frozen=True)
class ReviewTicket:
    flow_id: str
    status: Literal["pending_approval", "invalid"]
    order_id: str
    facts: dict
    message: str = ""


def _persistence(path) -> SQLiteFlowPersistence:
    return SQLiteFlowPersistence(str(path))


def start_review(order_id: str, requested_by: str, store: ReviewStore, flow_db=DEFAULT_FLOW_DB) -> ReviewTicket:
    """依頼を受け付け、承認待ちで Flow を止める。対象にできない注文なら、その場で invalid を返す。"""
    check_actor_id(requested_by)
    flow = ReviewFlow(store, persistence=_persistence(flow_db))
    result = flow.kickoff(inputs={"order_id": order_id, "requested_by": requested_by})
    if isinstance(result, HumanFeedbackPending):
        return ReviewTicket(flow.state.id, "pending_approval", order_id, dict(flow.state.facts))
    try:
        validate_review_target(order_id)
        message = ""
    except InvalidReviewTarget as e:
        message = e.message
    return ReviewTicket(flow.state.id, "invalid", order_id, {}, message)


def resolve_review(flow_id: str, decision: Literal["approve", "reject"], approver_id: str, store: ReviewStore,
                   flow_db=DEFAULT_FLOW_DB) -> str:
    """承認待ちの Flow を、承認または却下で再開する。結果（ReviewState.result）を返す。

    アプリを再起動したあとでも、別のインスタンスとして再開できる。
    """
    if decision not in (APPROVE, REJECT):
        raise ValueError(f"decision は {APPROVE!r} か {REJECT!r} のどちらかです")
    check_actor_id(approver_id)
    flow = ReviewFlow.from_pending(flow_id, _persistence(flow_db), store=store)  # 保留がなければ ValueError（二重の再開もここで止まる）
    if approver_id == flow.state.requested_by:
        store.audit("self_approval_blocked", flow_id, flow.state.order_id, approver_id)
        raise SelfApprovalError("依頼した本人は承認できません。別の承認者が操作してください。")
    flow.state.approved_by = approver_id
    flow.resume(decision)
    return flow.state.result
