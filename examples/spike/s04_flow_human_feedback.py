"""Spike 4: Flow + Human Feedback による承認 (第8章 / Evaluation Case 05)。

人の入力は ScriptedProvider で自動化し、承認・却下の両シナリオを検証する。
"""
from crewai.flow import Flow, HumanFeedbackProvider, human_feedback, listen, start
from pydantic import BaseModel

from common import MODEL

EXECUTED: list[str] = []  # Side Effect が実行された記録（本番では review_requests テーブル等）


class ScriptedProvider(HumanFeedbackProvider):
    def __init__(self, reply: str):
        self.reply = reply

    def request_feedback(self, context, flow) -> str:
        print(f"  [承認依頼] {context.method_output}")
        return self.reply


class ReviewState(BaseModel):
    order_id: str = ""
    reason: str = ""


def build_flow(reply: str) -> Flow:
    class ReviewFlow(Flow[ReviewState]):
        @start()
        def prepare(self):
            self.state.order_id = "O0001"
            self.state.reason = "高額注文の確認"

        @listen(prepare)
        @human_feedback(
            message="次のレビュー依頼を登録してよいですか？",
            emit=["approved", "rejected"],
            llm=MODEL,
            default_outcome="rejected",  # 判断不能なら実行しない（拒否がDefault）
            provider=ScriptedProvider(reply),
        )
        def propose(self):
            return f"order_id={self.state.order_id}, reason={self.state.reason}"

        @listen("approved")
        def execute(self):
            EXECUTED.append(self.state.order_id)
            return "executed"

        @listen("rejected")
        def skip(self):
            return "skipped"

    return ReviewFlow()


for label, reply in [("approve", "はい、登録してください。"), ("reject", "いいえ、登録しないでください。"),
                     ("ambiguous", "うーん、ちょっと考えさせて。")]:
    EXECUTED.clear()
    flow = build_flow(reply)
    result = flow.kickoff()
    fb = flow.last_human_feedback
    print(f"{label}: result={result!r} outcome={fb.outcome if fb else None} executed={EXECUTED}")
