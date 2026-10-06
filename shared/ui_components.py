"""画面の部品（第4章で導入）。分析結果の表示は、複数の画面（章）で共通に使う。"""
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
