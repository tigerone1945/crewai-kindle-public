# 第7章 GuardrailsとValidationで安全境界を作る

安全の境界を、2か所に置きます。どちらも LLM の外側で、コードで検査します。

| 場所 | 検査 | 止めたとき |
|---|---|---|
| 入力（Crew の前） | 空・長さ・秘密情報・個人情報・指示の上書き・範囲外 | `InputRejected`。理由を利用者へ伝える。0ミリ秒・0 Token |
| 出力（Task Guardrail） | Schema・数値の裏づけ・秘密情報／個人情報 | 却下の理由を加えて Crew を作り直す（最大3回）。使い切ると `AnalysisError`。Task 内の再生成は使わない（効果が低い。Spike s17） |

Guardrail は、Business Rule（`sales_amount` を使う等。Tool が守る）とも、Authorization（誰に許すか。第16章）とも別物です。

## 起動

```bash
uv run streamlit run chapters/ch07-guardrails/app.py
```

- 「Pythonでシューティングゲームを作ってください。」→ 案内が出て、Trace は `rejected`（LLM 0回）。
- 秘密情報（例: `sk-…`）やメールアドレスを含む質問 → 止まる。画面にも Trace にも、Memory の検索にも渡らない。
- 「では一番高かったのは？」のような続きの質問は、会話が始まっているときだけ通る。

## 確認

```bash
uv run pytest tests/unit                                    # 入力・出力の検査、サービス、画面（LLMを呼ばない）
uv run pytest tests/integration/test_ch07_guardrails.py     # 実Crew。Case 04、Guardrailの動作（APIキーが必要）
uv run python examples/spike/s14_guardrail_measure.py       # 通常質問での再生成、言い換えインジェクションの実測
uv run python examples/spike/s17_guardrail_feedback.py 10 nopref 3 0   # 失敗しやすい質問で、再生成の方式を比べる（引数: 回数 好み 作り直し回数 Task内再生成回数）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `app.py` | 第6章の画面に入力Guardrailと拒否の表示を追加 |
| `../../shared/guardrails.py` | `check_question`（入力）・`OutputGuard`（Task Guardrail）・`InputRejected` |
| `../../shared/analysis_service.py` | Task Guardrail で検査し、却下されたら理由を加えて Crew を作り直す `run_analysis` |
| `../../docs/figures/ch07-guardrail-rejected.jpg` | 実行画面（本文の図） |

## 注意

- 範囲外の判定は語彙による粗い関所です。「ゲームを作って。売上も教えて」は通ります。
- 指示の上書きの検出は、既知の表現の一覧です。言い換えは通ります。防御の中心は、Tool を目的限定にする構造と、出力の検査です。
- CrewAI の Guardrail は、Task を作るときに渡し、関数か束縛メソッドで渡します（`__call__` を持つインスタンスは不可）。
