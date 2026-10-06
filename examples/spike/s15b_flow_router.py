"""Spike 15: 第8章用。Flow を Human Feedback で一時停止し、別インスタンスで再開する（Streamlit のボタン承認に必要）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai.flow import Flow, HumanFeedbackPending, HumanFeedbackProvider, human_feedback, listen, router, start
from crewai.flow.persistence import SQLiteFlowPersistence
from pydantic import BaseModel

from shared.sales_data import MODEL
from shared.tracing import trace_run

EXECUTED = []


class PauseProvider(HumanFeedbackProvider):
    def request_feedback(self, context, flow):
        raise HumanFeedbackPending(context=context)


class State(BaseModel):
    order_id: str = ""


class ReviewFlow(Flow[State]):
    @start()
    @human_feedback(message="登録してよいですか？", provider=PauseProvider())  # emit なし = LLM による分類をしない
    def propose(self):
        self.state.order_id = "O0001"
        return "proposal O0001"

    @router(propose)
    def decide(self):
        fb = self.last_human_feedback
        return "approved" if fb is not None and fb.feedback.strip() == "approve" else "rejected"  # 完全一致のみ承認

    @listen("approved")
    def execute(self):
        EXECUTED.append(self.state.order_id)
        return "executed"

    @listen("rejected")
    def skip(self):
        return "skipped"


tmp = tempfile.mkdtemp()
db = str(Path(tmp) / "flows.db")
for reply in ["approve", "reject", "", "approve if the amount is small", "はい"]:
    EXECUTED.clear()
    persistence = SQLiteFlowPersistence(db)
    flow = ReviewFlow(persistence=persistence)
    try:
        first = flow.kickoff()
        kind = type(first).__name__
    except HumanFeedbackPending as e:
        first, kind = e, "raised:" + type(e).__name__
    flow_id = flow.state.id
    pending = persistence.load_pending_feedback(flow_id)
    # 別インスタンス（別プロセス相当）で再開
    flow2 = ReviewFlow.from_pending(flow_id, SQLiteFlowPersistence(db))
    with trace_run() as t:
        result = flow2.resume(reply)
    fb = flow2.last_human_feedback
    print(f"reply={reply!r}: first={kind} pending_saved={pending is not None} -> result={result!r} feedback={fb.feedback if fb else None!r} "
          f"executed={EXECUTED} llm_calls={t.summary()['llm_calls']} state_order={flow2.state.order_id!r}")
# 二重再開
try:
    flow3 = ReviewFlow.from_pending(flow_id, SQLiteFlowPersistence(db))
    print("second resume of same flow:", flow3.resume("approve"), EXECUTED)
except Exception as e:
    print("second resume error:", type(e).__name__, str(e)[:100])
