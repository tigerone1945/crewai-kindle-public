# 第2章 ToolでCSV売上データを分析する

売上の集計を、LLM ではなくコード（Tool）に任せます。Agent は質問に応じて Tool を選び、Tool の結果だけを根拠に説明します。

## 実行

リポジトリのルートで実行します。

```bash
uv run python chapters/ch02-tools/main.py "地域別の売上を比較してください。"
uv run python chapters/ch02-tools/main.py "売上全体の状況を教えてください。"
```

質問を省略すると「カテゴリ別の売上を教えてください。」で実行します。

## 確認

```bash
uv run pytest tests/unit        # 集計・Tool（LLMを呼ばない）
uv run pytest tests/integration # 共通 Evaluation Case 01 / 02（LLMを呼ぶ。OPENAI_API_KEY が必要）
```

- 売上合計 28,298,695円、キャンセル 14件、要レビュー 37件
- 地域別・カテゴリ別の売上が期待値と一致（`sales_amount` 基準。`total_amount` を使わない）
- キャンセル注文が売上に含まれない
- Tool に任意のコード・SQL を実行するものがない

## ファイル

| ファイル | 内容 |
|---|---|
| `main.py` | Agent / Task / Crew（Tool を持つ Agent） |
| `sales_tools.py` | CrewAI Tool の定義（集計関数の薄いラッパー） |
| `../../shared/sales_analytics.py` | 集計ロジック（pandas）。単体テスト可能な純粋関数 |
| `../../shared/tool_calls.py` | 呼ばれた Tool の記録（検証用） |
| `../../evaluations/checks.py` | Evaluation Case の期待値の機械判定 |
