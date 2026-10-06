# 第6章 Execution TraceでCrewの動きを追う

1回の実行で、Agent・Task・Tool・LLM 呼び出しが何をしたかを記録します。Trace に質問文・プロンプト・Tool の出力の中身は残しません。

| | Trace | Application Log |
|---|---|---|
| 単位 | 1回の実行（`data/traces/<run_id>.json`） | アプリケーション全体（`logging`） |
| 問い | この実行で何が起きたか | 異常はないか |
| つなぎ方 | `run_id` | ログに `run_id=…` を出す |

## 起動

```bash
uv run streamlit run chapters/ch06-execution-trace/app.py
```

回答の下の「実行の記録（Trace）」を開くと、所要時間・LLM 呼び出し回数・Token・試行回数・呼ばれた Tool と、1件ずつの一覧が見られます。保存先は `data/traces/`（Git の対象外）です。

## コードから使う

```python
from shared.tracing import trace_run

with trace_run() as trace:
    result = service.run_analysis(question)
print(trace.summary())
```

## 確認

```bash
uv run pytest tests/unit                              # Event Bus に本物の Event を流す（LLMを呼ばない）
uv run pytest tests/integration/test_ch06_trace.py    # 本物のCrew実行を記録する（APIキーが必要）
uv run python examples/spike/s12_trace_breakdown.py   # 所要時間の内訳を5回測る（本文の実測）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `app.py` | 第5章の画面に Trace の表示と保存を追加 |
| `../../shared/tracing.py` | `trace_run`・`RunTrace`・`TraceCollector`（Event Bus の購読） |
| `../../shared/sensitive.py` | 秘密情報・個人情報の検出とマスク（Memory Policy と共通） |
| `../../shared/ui_components.py` | `render_trace`（Trace の表示） |
| `../../docs/figures/ch06-execution-trace.jpg` | 実行画面（本文の図） |

## 注意

- 本番では `verbose=True` を使いません。Tool の出力がコンソールへ表示されます。
- Trace の保管期間と削除、外部サービスへの送信は第17章で扱います。
