"""第4章 StreamlitでHuman Interfaceを作る。

画面は「入力・送信・表示」だけを担当し、分析ロジックは shared/analysis_service.py に任せる。
起動: uv run streamlit run chapters/ch04-streamlit-ui/app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service  # noqa: E402
from shared.ui_components import render_result  # noqa: E402

logger = logging.getLogger("sales_ui")

MAX_QUESTION_LENGTH = 500
EXAMPLES = (
    "売上全体の状況を分析してください。",
    "カテゴリ別の売上を分析してください。",
    "地域別の売上を比較してください。",
)


def _use_example(text: str) -> None:
    st.session_state["question"] = text


def handle_submit(question: str) -> None:
    question = question.strip()
    if not question:
        st.warning("分析したい内容を入力してください。")
        return
    if len(question) > MAX_QUESTION_LENGTH:
        st.warning(f"質問は{MAX_QUESTION_LENGTH}文字以内で入力してください。")
        return

    with st.spinner("AIエージェントが分析しています..."):
        try:
            result = service.run_analysis(question)
        except service.AnalysisError:
            logger.exception("analysis rejected by validation")
            st.warning("正確な回答を生成できませんでした。質問の表現を変えて、もう一度お試しください。")
            return
        except Exception:
            logger.exception("analysis failed")  # 詳細はログへ。画面には内部情報を出さない
            st.error("分析中にエラーが発生しました。時間をおいて、もう一度お試しください。")
            return

    st.success("分析が完了しました。")
    render_result(result)


def main() -> None:
    st.set_page_config(page_title="AI売上分析アシスタント", page_icon="📊")
    st.title("AI売上分析アシスタント")
    st.caption("CrewAI + pandas + Streamlit")

    st.write("質問例:")
    for i, example in enumerate(EXAMPLES):
        st.button(example, key=f"example_{i}", on_click=_use_example, args=(example,))

    with st.form("analysis_form"):
        question = st.text_area(
            "分析したい内容を入力してください",
            key="question",
            value=EXAMPLES[1],
            height=100,
        )
        submitted = st.form_submit_button("分析する")

    if submitted:
        handle_submit(question)


main()
