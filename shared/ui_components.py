"""画面の部品（第4章で導入）。分析結果の表示は、複数の画面（章）で共通に使う。"""
import pandas as pd
import streamlit as st

from shared.schemas import SalesAnalysisResult
from shared.tracing import RunTrace


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


def trace_table(trace: RunTrace) -> pd.DataFrame:
    """Traceを表にする。終了した順に並ぶため、内側の処理（LLM・Tool）が外側（Task・Crew）より先に出る。"""
    def detail(e) -> str:
        parts = [f"{k}={v}" for k, v in e.attrs.items() if v not in (None, "", False)]
        return ", ".join(parts)

    return pd.DataFrame(
        [{"種別": e.kind, "名前": e.name, "状態": e.status, "所要(ms)": e.duration_ms, "詳細": detail(e)} for e in trace.events]
    )


def render_trace(trace: RunTrace) -> None:
    s = trace.summary()
    with st.expander(f"実行の記録（Trace） run_id={s['run_id']}"):
        cols = st.columns(5)
        cols[0].metric("所要時間", "-" if s["duration_ms"] is None else f"{s['duration_ms'] / 1000:.1f} 秒")
        cols[1].metric("LLM呼び出し", s["llm_calls"])
        cols[2].metric("入力Token", f"{s['prompt_tokens']:,}")
        cols[3].metric("出力Token", f"{s['completion_tokens']:,}")
        cols[4].metric("試行 / エラー", f"{s['attempts']} / {s['errors']}")
        st.caption(f"状態: {s['status']} / 呼ばれたTool: " + (", ".join(s["tool_calls"]) or "なし"))
        st.dataframe(trace_table(trace), hide_index=True, width="stretch")
        st.caption("質問文・プロンプト・Toolの出力の中身は記録していません。")
