"""画面の部品（第4章で導入）。分析結果の表示は、複数の画面（章）で共通に使う。"""
import pandas as pd
import streamlit as st

from shared.result_view import metrics_table, render_result  # noqa: F401  既存の import 先を保つための再公開
from shared.tracing import RunTrace


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
