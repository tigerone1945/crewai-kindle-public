"""分析結果の表示（第4章で導入、第14章で分離）。Streamlit と pandas だけに依存する。

API の利用側（第14章の UI）は、CrewAI を import しない。Trace の表示（shared/ui_components.py）は CrewAI に依存するため、
結果の表示だけをここへ分けた。
"""
import pandas as pd
import streamlit as st

from shared.schemas import SalesAnalysisResult


def metrics_table(result: SalesAnalysisResult) -> pd.DataFrame:
    def fmt(value: float, unit: str) -> str:
        return f"{value:.1f}" if unit == "%" else f"{value:,.0f}"

    return pd.DataFrame(
        [{"指標": m.label, "値": fmt(m.value, m.unit), "単位": m.unit} for m in result.metrics]
    )


def render_result(result: SalesAnalysisResult) -> None:
    if result.analysis_type == "unsupported":
        st.info("この質問には対応できません。売上・受注データの集計に関する質問をしてください。")
        st.write(result.summary)
        return

    st.subheader("分析概要")
    st.write(result.summary)
    st.subheader("指標")
    st.dataframe(metrics_table(result), hide_index=True, width="stretch")
    st.subheader("重要な発見")
    for item in result.insights:
        st.markdown(f"- {item}")
    st.subheader("次に確認すること")
    for item in result.recommendations:
        st.markdown(f"- {item}")
    with st.expander("Structured Output（JSON）"):
        st.json(result.model_dump())
