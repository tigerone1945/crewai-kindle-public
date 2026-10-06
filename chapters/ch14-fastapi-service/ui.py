"""第14章 UIとAPIの分離。

画面（Streamlit）は、分析サービス（FastAPI）を HTTP で呼ぶだけ。CrewAI・Flow・DB・Memory は、画面のプロセスにない。
画面が知っているのは、API の契約（shared/api_contract.py）と、利用者に見せてよい文だけ。
起動（2つのプロセス）:
  uv run uvicorn --factory shared.api:create_app --host 127.0.0.1 --port 8000
  uv run streamlit run chapters/ch14-fastapi-service/ui.py
接続先は、環境変数 ANALYSIS_API_URL（既定 http://127.0.0.1:8000）。
"""
import logging
import sys
from pathlib import Path

import streamlit as st

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.api_client import AnalysisApiClient, ApiError  # noqa: E402
from shared.api_contract import HistoryTurn  # noqa: E402
from shared.result_view import render_result  # noqa: E402

logger = logging.getLogger("sales_ui")
DEFAULT_ACTOR_ID = "user-001"  # 本番では認証済みの利用者IDから決める（第16章）
DEFAULT_APPROVER_ID = "approver-001"
MAX_HISTORY_TURNS = 5

RESULT_MESSAGES = {
    "registered": ("success", "レビュー対象として登録しました。"),
    "cancelled": ("info", "却下しました。何も登録していません。"),
    "duplicate": ("warning", "すでに同じ注文のレビュー依頼があるため、登録しませんでした。"),
    "not_eligible": ("warning", "注文の状態が変わり、対象にできなくなったため、登録しませんでした。"),
}
ROUTE_LABELS = {"single": "単一の Agent で分析", "integrated": "データ品質を含む、3つの Role で分析", "review_request": "承認が必要な依頼（承認 Flow へ）"}


def get_client() -> AnalysisApiClient:
    if "client" not in st.session_state:
        st.session_state["client"] = AnalysisApiClient()
    return st.session_state["client"]


def valid_id(label: str, default: str, **kwargs) -> str:
    value = st.text_input(label, value=default, **kwargs)
    if not value or not all(c.isalnum() or c in "-_" for c in value) or len(value) > 64:
        st.error(f"{label}は、英数字・ハイフン・アンダースコアの1〜64文字にしてください。")
        st.stop()
    return value


def sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.subheader("利用者")
        actor_id = valid_id("利用者ID", DEFAULT_ACTOR_ID)
        approver_id = valid_id("承認者ID", DEFAULT_APPROVER_ID)

        st.subheader("好みを覚えさせる")
        st.caption("表現や形式の好みだけを保存できます。売上の数値は保存されません。")
        with st.form("preference_form", clear_on_submit=True):
            text = st.text_input("例: 要約は必ず「結論:」で始める")
            if st.form_submit_button("登録"):
                try:
                    get_client().add_preference(actor_id, text)
                    st.success("登録しました。")
                except ApiError as e:
                    st.warning(f"登録できません: {e.message}")

        st.subheader("登録済みの好み")
        try:
            saved = get_client().preferences(actor_id)
        except ApiError as e:
            saved = []
            st.error(e.message)
        if not saved:
            st.caption("まだありません。")
        for item in saved:
            col1, col2 = st.columns([4, 1])
            col1.write(item.content)
            if col2.button("削除", key=f"del_{item.id}"):
                try:
                    get_client().delete_preference(actor_id, item.id)
                except ApiError as e:
                    st.error(e.message)
                st.rerun()

        if st.button("新しい会話を始める"):
            st.session_state["messages"] = []
            st.session_state["turns"] = []
            st.rerun()
    return actor_id, approver_id


def add_notice(level: str, text: str, run_id: str | None = None, route: str | None = None) -> None:
    st.session_state["messages"].append({"role": "notice", "level": level, "text": text, "run_id": run_id, "route": route})


def handle_question(actor_id: str, question: str) -> None:
    question = question.strip()
    st.session_state["messages"].append({"role": "user", "question": question})
    history = [HistoryTurn(**t) for t in st.session_state["turns"][-MAX_HISTORY_TURNS:]]
    try:
        response = get_client().analyze(question, actor_id, history)
    except ValueError:  # 契約に合わない（空・長すぎる）。サービスへ送らずに、画面で止める
        add_notice("warning", "質問は、1〜2000文字で入力してください。")
        return
    except ApiError as e:
        # 利用者が直せる問題（入力の検査）は警告、それ以外は、サービスの状態の問題。内部の詳細は、サービスが返さない
        level = "warning" if e.code in ("input_rejected", "review_invalid", "analysis_failed", "busy", "unavailable", "timeout") else "error"
        suffix = f"（問い合わせ番号: {e.run_id}）" if e.run_id and level == "error" else ""
        logger.warning("api error code=%s status=%s run_id=%s", e.code, e.status, e.run_id)
        add_notice(level, e.message + suffix, e.run_id)
        return
    if response.kind == "review_pending":
        add_notice("info", response.message, response.run_id, response.route)
        return
    st.session_state["turns"].append({"question": question, "analysis_type": response.result.analysis_type, "summary": response.result.summary})
    st.session_state["messages"].append({"role": "assistant", "result": response.result, "run_id": response.run_id, "route": response.route})


def render_pending_approvals(approver_id: str) -> None:
    try:
        pending = get_client().pending_reviews()
    except ApiError as e:
        st.error(e.message)
        return
    if not pending:
        return
    st.subheader("承認待ちのレビュー依頼")
    st.caption("依頼した本人は承認できません。承認者IDを変えて操作してください。")
    for item in pending:
        with st.container(border=True):
            if item.order:
                o = item.order
                st.write(f"注文 {o['order_id']}（{o['order_date']} / {o['customer_type']} / {o['category']} / {o['status']} / 売上 {o['sales_amount']:,}円）")
            else:
                st.write(f"注文 {item.order_id}: {item.problem}")
            st.caption(f"依頼者: {item.requested_by}")
            col1, col2 = st.columns(2)
            for col, label, decision in ((col1, "承認", "approve"), (col2, "却下", "reject")):
                if col.button(label, key=f"{decision}_{item.flow_id}"):
                    resolve(item, decision, approver_id)


def resolve(item, decision: str, approver_id: str) -> None:
    try:
        outcome = get_client().decide(item.flow_id, decision, approver_id)
    except ApiError as e:
        if e.code == "already_resolved":  # 画面から消すために再描画する（通知は履歴へ残す。st.warning は再描画で消えるため）
            add_notice("warning", f"注文 {item.order_id}: この依頼は、すでに処理されています。")
            st.rerun()
        st.error(e.message)  # 本人による承認など。承認待ちのまま残る
        return
    level, text = RESULT_MESSAGES[outcome]
    add_notice(level, f"注文 {item.order_id}: {text}")
    st.rerun()


def render_run(message: dict) -> None:
    parts = []
    if message.get("route"):
        parts.append(f"経路: {ROUTE_LABELS[message['route']]}")
    if message.get("run_id"):
        parts.append(f"run_id={message['run_id']}（Trace はサービス側に保存）")
    if parts:
        st.caption(" / ".join(parts))


def render_history() -> None:
    for m in st.session_state["messages"]:
        if m["role"] == "user":
            with st.chat_message("user"):
                st.write(m["question"])
        elif m["role"] == "notice":
            with st.chat_message("assistant"):
                getattr(st, m["level"])(m["text"])  # info / success / warning / error
                render_run(m)
        else:
            with st.chat_message("assistant"):
                render_result(m["result"])
                render_run(m)


def main() -> None:
    st.set_page_config(page_title="AI売上分析アシスタント", page_icon="📊")
    st.title("AI売上分析アシスタント")
    st.caption("UI（Streamlit）と分析サービス（FastAPI）を分離")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("turns", [])

    actor_id, approver_id = sidebar()
    question = st.chat_input("売上データについて質問してください")
    if question is not None:
        with st.spinner("処理しています..."):
            handle_question(actor_id, question)
    render_pending_approvals(approver_id)
    render_history()


main()
