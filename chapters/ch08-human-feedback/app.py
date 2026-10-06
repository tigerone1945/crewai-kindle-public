"""第8章 FlowへHuman Feedbackを組み込む。

第7章の画面に、状態を変える処理（レビュー依頼の登録）と、人の承認を加える。
- 「O0001をレビュー対象として登録してください」→ Flow が承認待ちで止まる。この時点では何も登録されない。
- 承認待ちの一覧から、別の承認者が承認または却下する。承認されたときだけ登録される。
- 承認待ちは永続化されている。アプリを再起動しても、そのまま承認・却下できる。
起動: uv run streamlit run chapters/ch08-human-feedback/app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service  # noqa: E402
import shared.memory_policy as memory_policy  # noqa: E402
import shared.review_flow as review_flow  # noqa: E402
import shared.review_requests as review_requests  # noqa: E402
from shared.guardrails import InputRejected, check_question  # noqa: E402
from shared.conversation import Turn  # noqa: E402
from shared.tracing import RunTrace, trace_run  # noqa: E402
from shared.ui_components import render_result, render_trace  # noqa: E402

logger = logging.getLogger("sales_ui")
DEFAULT_ACTOR_ID = "user-001"  # 本番では認証済みの利用者IDから決める（第16章）
DEFAULT_APPROVER_ID = "approver-001"


def get_store() -> memory_policy.PreferenceStore:
    if "store" not in st.session_state:
        st.session_state["store"] = memory_policy.default_preference_store()
    return st.session_state["store"]


def get_review_store() -> review_requests.ReviewStore:
    return review_requests.ReviewStore(review_requests.DEFAULT_DB)


def sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.subheader("利用者")
        try:
            actor_id = memory_policy.check_actor_id(st.text_input("利用者ID", value=DEFAULT_ACTOR_ID))
        except ValueError as e:
            st.error(str(e))
            st.stop()

        try:
            approver_id = memory_policy.check_actor_id(st.text_input("承認者ID", value=DEFAULT_APPROVER_ID))
        except ValueError as e:
            st.error(str(e))
            st.stop()

        st.subheader("好みを覚えさせる")
        st.caption("表現や形式の好みだけを保存できます。売上の数値は保存されません。")
        with st.form("preference_form", clear_on_submit=True):
            text = st.text_input("例: 要約は必ず「結論:」で始める")
            if st.form_submit_button("登録"):
                try:
                    get_store().remember_preference(actor_id, text)
                    st.success("登録しました。")
                except memory_policy.PolicyViolation as e:
                    st.warning(f"登録できません: {e}")
                except Exception:
                    logger.exception("preference save failed")
                    st.error("登録中にエラーが発生しました。")

        st.subheader("登録済みの好み")
        try:
            saved = get_store().list_preferences(actor_id)
        except Exception:
            logger.exception("preference list failed")
            saved = []
            st.error("好みを取得できませんでした。")
        if not saved:
            st.caption("まだありません。")
        for record_id, content in saved:
            col1, col2 = st.columns([4, 1])
            col1.write(content)
            if col2.button("削除", key=f"del_{record_id}"):
                get_store().delete_preference(actor_id, record_id)
                st.rerun()

        if st.button("新しい会話を始める"):
            st.session_state["messages"] = []
            st.session_state["turns"] = []
            st.rerun()
    return actor_id, approver_id


def request_review(actor_id: str, order_ids: list[str]) -> tuple[str, str]:
    if len(order_ids) > 1:
        return "warning", "レビュー依頼は、一度に1件ずつ登録してください。"
    ticket = review_flow.start_review(order_ids[0], actor_id, get_review_store(), review_flow.DEFAULT_FLOW_DB)
    if ticket.status == "invalid":
        return "warning", ticket.message
    return "info", f"注文 {ticket.order_id} のレビュー依頼を、承認待ちにしました。承認されるまで、登録はされません。"


RESULT_MESSAGES = {
    "registered": ("success", "レビュー対象として登録しました。"),
    "cancelled": ("info", "却下しました。何も登録していません。"),
    "duplicate": ("warning", "すでに同じ注文のレビュー依頼があるため、登録しませんでした。"),
    "not_eligible": ("warning", "注文の状態が変わり、対象にできなくなったため、登録しませんでした。"),
}


def render_pending_approvals(approver_id: str) -> None:
    pending = get_review_store().pending_approvals()
    if not pending:
        return
    st.subheader("承認待ちのレビュー依頼")
    st.caption("依頼した本人は承認できません。承認者IDを変えて操作してください。")
    for item in pending:
        with st.container(border=True):
            try:
                facts = review_requests.validate_review_target(item["order_id"])
                st.write(f"注文 {facts['order_id']}（{facts['order_date']} / {facts['customer_type']} / {facts['category']} / "
                         f"{facts['status']} / 売上 {facts['sales_amount']:,}円）")
            except review_requests.InvalidReviewTarget as e:
                st.write(f"注文 {item['order_id']}: {e.message}")
            st.caption(f"依頼者: {item['requested_by']}")
            col1, col2 = st.columns(2)
            for col, label, decision in ((col1, "承認", review_flow.APPROVE), (col2, "却下", review_flow.REJECT)):
                if col.button(label, key=f"{decision}_{item['flow_id']}"):
                    resolve_pending(item, decision, approver_id)


def resolve_pending(item: dict, decision: str, approver_id: str) -> None:
    try:
        outcome = review_flow.resolve_review(item["flow_id"], decision, approver_id, get_review_store(), review_flow.DEFAULT_FLOW_DB)
    except review_flow.SelfApprovalError as e:
        st.error(str(e))
        return
    except ValueError:  # すでに他の承認者が決めた
        logger.warning("review already resolved flow_id=%s", item["flow_id"])
        st.session_state["messages"].append({"role": "notice", "level": "warning", "trace": None,
                                              "text": f"注文 {item['order_id']}: この依頼は、すでに処理されています。"})
        st.rerun()  # 画面から消すために再描画する（通知は履歴へ残す。st.warning は再描画で消えるため）
        return
    except Exception:
        logger.exception("review resolve failed flow_id=%s", item["flow_id"])
        st.error("処理中にエラーが発生しました。時間をおいて、もう一度お試しください。")
        return
    level, text = RESULT_MESSAGES[outcome]
    st.session_state["messages"].append({"role": "notice", "level": level, "text": f"注文 {item['order_id']}: {text}", "trace": None})
    st.rerun()


def handle_question(actor_id: str, question: str) -> None:
    result, preferences, notice = None, [], None
    with trace_run() as trace:
        try:
            # 入力の検査は、Memory の検索より前に行う。秘密情報や個人情報を、検索（外部のEmbedding）へ渡さないため
            question = check_question(question, has_history=bool(st.session_state["turns"]))
            st.session_state["messages"].append({"role": "user", "question": question})
            order_ids = review_flow.extract_review_order_ids(question)
            if order_ids:  # 状態を変える依頼。Agent には渡さず、承認 Flow へ進める
                notice = request_review(actor_id, order_ids)
            else:
                preferences = get_store().recall_preferences(actor_id, question)  # 取得に失敗しても空で動く
                result = service.run_analysis(question, history=st.session_state["turns"], preferences=preferences)
        except InputRejected as e:  # 利用者が直せる問題。理由をそのまま伝える。異常ではないのでログは警告にとどめる
            logger.warning("input rejected code=%s run_id=%s", e.code, trace.run_id)
            notice = ("warning", e.message)
        except service.AnalysisError:
            logger.exception("analysis rejected by output guardrail run_id=%s", trace.run_id)
            notice = ("warning", "正確な回答を生成できませんでした。質問の表現を変えて、もう一度お試しください。")
        except Exception:
            logger.exception("request failed run_id=%s", trace.run_id)
            notice = ("error", "処理中にエラーが発生しました。時間をおいて、もう一度お試しください。")
    save_trace(trace)

    if notice is not None:
        level, text = notice
        st.session_state["messages"].append({"role": "notice", "level": level, "text": text, "trace": trace})
        return
    st.session_state["turns"].append(Turn.from_result(question, result))
    st.session_state["messages"].append({"role": "assistant", "result": result, "preferences": preferences, "trace": trace})


def save_trace(trace: RunTrace) -> None:
    """Traceを保存する。記録の失敗で利用者への回答を止めない（Traceは観測であり、業務処理ではない）。"""
    try:
        trace.save()
    except OSError:
        logger.exception("trace save failed run_id=%s", trace.run_id)


def render_history() -> None:
    for m in st.session_state["messages"]:
        if m["role"] == "user":
            with st.chat_message("user"):
                st.write(m["question"])
        elif m["role"] == "notice":
            with st.chat_message("assistant"):
                getattr(st, m["level"])(m["text"])  # info / success / warning / error
                if m["trace"] is not None:
                    render_trace(m["trace"])
        else:
            with st.chat_message("assistant"):
                render_result(m["result"])
                if m["preferences"]:
                    st.caption("適用した好み: " + " / ".join(m["preferences"]))
                render_trace(m["trace"])


def main() -> None:
    st.set_page_config(page_title="AI売上分析アシスタント", page_icon="📊")
    st.title("AI売上分析アシスタント")
    st.caption("CrewAI + Flow（Human Feedback）+ Guardrails")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("turns", [])

    actor_id, approver_id = sidebar()
    question = st.chat_input("売上データについて質問してください")
    if question is not None:
        with st.spinner("処理しています..."):
            handle_question(actor_id, question)
    render_pending_approvals(approver_id)  # 質問の処理のあとに描く（いま作られた承認待ちも出すため）
    render_history()


main()
