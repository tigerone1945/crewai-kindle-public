"""第7章 GuardrailsとValidationで安全境界を作る。

第6章の画面に、安全の境界を加える。
- 入力: Crew を呼ぶ前に検査する（範囲外・秘密情報・個人情報・指示の上書き）。止めた理由は利用者へ伝え、Memory の検索にも渡さない。
- 出力: Task Guardrail が検査し、通らなければ同じ Task の中で再生成させる（shared/guardrails.py）。
起動: uv run streamlit run chapters/ch07-guardrails/app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service  # noqa: E402
import shared.memory_policy as memory_policy  # noqa: E402
from shared.guardrails import InputRejected, check_question  # noqa: E402
from shared.conversation import Turn  # noqa: E402
from shared.tracing import RunTrace, trace_run  # noqa: E402
from shared.ui_components import render_result, render_trace  # noqa: E402

logger = logging.getLogger("sales_ui")
DEFAULT_ACTOR_ID = "user-001"  # 本番では認証済みの利用者IDから決める（第16章）


def get_store() -> memory_policy.PreferenceStore:
    if "store" not in st.session_state:
        st.session_state["store"] = memory_policy.default_preference_store()
    return st.session_state["store"]


def sidebar() -> str:
    with st.sidebar:
        st.subheader("利用者")
        try:
            actor_id = memory_policy.check_actor_id(st.text_input("利用者ID", value=DEFAULT_ACTOR_ID))
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
    return actor_id


def handle_question(actor_id: str, question: str) -> None:
    with trace_run() as trace:
        try:
            # 入力の検査は、Memory の検索より前に行う。秘密情報や個人情報を、検索（外部のEmbedding）へ渡さないため
            question = check_question(question, has_history=bool(st.session_state["turns"]))
            st.session_state["messages"].append({"role": "user", "question": question})
            preferences = get_store().recall_preferences(actor_id, question)  # 取得に失敗しても空で動く
            result = service.run_analysis(question, history=st.session_state["turns"], preferences=preferences)
        except InputRejected as e:  # 利用者が直せる問題。理由をそのまま伝える。異常ではないのでログは警告にとどめる
            logger.warning("input rejected code=%s run_id=%s", e.code, trace.run_id)
            result, notice = None, ("warning", e.message)
        except service.AnalysisError:
            logger.exception("analysis rejected by output guardrail run_id=%s", trace.run_id)
            result, notice = None, ("warning", "正確な回答を生成できませんでした。質問の表現を変えて、もう一度お試しください。")
        except Exception:
            logger.exception("analysis failed run_id=%s", trace.run_id)
            result, notice = None, ("error", "分析中にエラーが発生しました。時間をおいて、もう一度お試しください。")
    save_trace(trace)

    if result is None:
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
                (st.warning if m["level"] == "warning" else st.error)(m["text"])
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
    st.caption("CrewAI + Memory + Execution Trace + Guardrails")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("turns", [])

    actor_id = sidebar()
    question = st.chat_input("売上データについて質問してください")
    if question is not None:
        with st.spinner("AIエージェントが分析しています..."):
            handle_question(actor_id, question)
    render_history()


main()
