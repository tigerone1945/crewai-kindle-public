# 第13章 Flowで決定論的なWorkflowを作る

質問を受けてから結果を返すまでの流れを、1つの Flow（`shared/analysis_flow.py`）にまとめます。Crew = Agency、Flow = Deterministic Backbone。

```text
receive ─ route ─ rejected ──────────────────────→ （終了。LLM を呼ばない）
                ├ review_request → request_review（承認 Flow へ。第8章）
                ├ single ──────┐
                └ integrated ──┴→ run_analysis ─ evaluate ─ done
                                        ↑            ├ retry（通信などの一時的な失敗。最大2回）─┘
                                        └────────────┴ fallback → escalate（人へ引き継ぐ）
```

Flow が固定すること: 入力を受け付けるか／経路／何を再試行するか／いつ人に引き継ぐか／承認が要る処理。Crew が担うこと: 経路の中の分析。

## 使い方

```python
from shared.analysis_flow import run_question

outcome = run_question("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。", "user-001",
                       review_store=store)
outcome.kind    # analysis / rejected / review_pending / review_invalid / escalated
outcome.route   # rejected / review_request / single / integrated
```

画面は `kind` で表示を出し分けるだけです。画面: `uv run streamlit run chapters/ch13-flow/app.py`

## 測ったこと

| | 結果 |
|---|---|
| 経路の選択（24問） | 決定論の規則 17/24、LLM 63/72。規則の誤りは通常の分析へ流れる方向が多く、LLM は読み取りの質問を承認の経路へ回した |
| Flow のオーバーヘッド（各8回） | Token 3,704 → 3,705、時間 8.2 秒 → 7.7 秒（差はばらつきの範囲） |
| LLM の呼び出し | 範囲外・レビュー依頼は 0 回 |

## 確認

```bash
uv run pytest tests/unit/test_analysis_flow.py tests/unit/test_ch13_ui.py   # Flow・経路・再試行・画面（LLMを呼ばない）
uv run pytest tests/integration/test_ch13_flow.py                           # 経路を Trace で確認（APIキーが必要）
uv run python examples/spike/s22_flow_routing.py AB 3                       # 経路の比較（A）と Flow のオーバーヘッド（B）。APIキーが必要
```

## ファイル

| ファイル | 内容 |
|---|---|
| `../../shared/analysis_flow.py` | `AnalysisFlow`、`choose_route`、`run_question`、`FlowOutcome` |
| `app.py` | 画面（第8章の画面を、Flow 経由に変更） |
| `../../examples/spike/s22_flow_routing.py` | 経路の選択の比較と、オーバーヘッドの測定 |
