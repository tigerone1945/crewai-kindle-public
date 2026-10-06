"""第13章 分析の流れを Flow で決める。

第8章の画面の、質問を受けてから結果を返すまでの流れを、1つの Flow（shared/analysis_flow.py）にまとめる。
- 入力の検査 → 経路の選択（単一 Agent / データ品質を含む3つの Role / 承認が要る依頼）→ 実行 → 失敗時の再試行と引き継ぎ。
- 経路の選択・再試行・引き継ぎは、LLM ではなくコードで決まる。画面は、Flow の結果の種類（kind）で表示を出し分けるだけ。
- 承認待ちの一覧と、承認・却下は、第8章と同じ（承認 Flow）。
起動: uv run streamlit run chapters/ch13-flow/app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_flow as analysis_flow  # noqa: E402
import shared.memory_policy as memory_policy  # noqa: E402
import shared.review_flow as review_flow  # noqa: E402
import shared.review_requests as review_requests  # noqa: E402
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


ROUTE_LABELS = {
    "single": "単一の Agent で分析", "integrated": "データ品質を含む、3つの Role で分析",
    "review_request": "承認が必要な依頼（承認 Flow へ）", "rejected": "入力の検査で停止",
}
OUTCOME_LEVELS = {"rejected": "warning", "review_pending": "info", "review_invalid": "warning", "escalated": "warning"}


def handle_question(actor_id: str, question: str) -> None:
    outcome = None
    with trace_run() as trace:
        try:
            # 入力の検査・経路の選択・再試行は、Flow が行う。入力の検査は、Memory の検索より前（Flow の中で保たれる）
            outcome = analysis_flow.run_question(
                question, actor_id, review_store=get_review_store(), flow_db=review_flow.DEFAULT_FLOW_DB,
                history=st.session_state["turns"], preferences_for=lambda q: get_store().recall_preferences(actor_id, q))
        except Exception:
            logger.exception("request failed run_id=%s", trace.run_id)
    save_trace(trace)

    if outcome is None:
        st.session_state["messages"].append({"role": "user", "question": question.strip()})
        st.session_state["messages"].append({"role": "notice", "level": "error", "trace": trace,
                                              "text": "処理中にエラーが発生しました。時間をおいて、もう一度お試しください。"})
        return
    st.session_state["messages"].append({"role": "user", "question": question.strip()})
    if outcome.kind != "analysis":
        st.session_state["messages"].append({"role": "notice", "level": OUTCOME_LEVELS[outcome.kind], "text": outcome.message,
                                              "trace": trace, "route": outcome.route})
        return
    st.session_state["turns"].append(Turn.from_result(question.strip(), outcome.result))
    st.session_state["messages"].append({"role": "assistant", "result": outcome.result, "preferences": [], "trace": trace,
                                          "route": outcome.route})


def save_trace(trace: RunTrace) -> None:
    """Traceを保存する。記録の失敗で利用者への回答を止めない（Traceは観測であり、業務処理ではない）。"""
    try:
        trace.save()
    except OSError:
        logger.exception("trace save failed run_id=%s", trace.run_id)


def render_route(message: dict) -> None:
    if message.get("route"):
        st.caption(f"経路: {ROUTE_LABELS[message['route']]}（経路の選択は、コードで決定。LLM は使っていません）")


def render_history() -> None:
    for m in st.session_state["messages"]:
        if m["role"] == "user":
            with st.chat_message("user"):
                st.write(m["question"])
        elif m["role"] == "notice":
            with st.chat_message("assistant"):
                getattr(st, m["level"])(m["text"])  # info / success / warning / error
                if m["trace"] is not None:
                    render_route(m)
                    render_trace(m["trace"])
        else:
            with st.chat_message("assistant"):
                render_result(m["result"])
                render_route(m)
                render_trace(m["trace"])


def main() -> None:
    st.set_page_config(page_title="AI売上分析アシスタント", page_icon="📊")
    st.title("AI売上分析アシスタント")
    st.caption("CrewAI + Flow（経路・再試行・引き継ぎ・承認）+ Guardrails")
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
