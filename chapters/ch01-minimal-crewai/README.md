# 第1章 CrewAIの最小構成を作る

Agent / Task / Crew の最小構成で、売上CSVの Dataset Profile から「最初に確認すべき分析観点」を提案させます。
この章の Agent は数値を集計しません（第2章で Tool へ委譲します）。

## 実行

リポジトリのルートで実行します。

```bash
uv sync
cp .env.example .env   # OPENAI_API_KEY を設定
uv run python chapters/ch01-minimal-crewai/main.py
```

## 確認

```bash
uv run pytest tests/unit/test_dataset_profile.py
```

- Dataset Profile に件数・列名・期間が含まれる
- 集計値や値の一覧（法人 / 東北 など）が含まれない
- 空データ・必須列欠落・CSV 不在は明確なエラーになる

## ファイル

| ファイル | 内容 |
|---|---|
| `main.py` | Agent / Task / Crew の定義と実行 |
| `../../shared/dataset_profile.py` | Dataset Profile の生成（Agent へ渡す要約） |
| `../../shared/sales_data.py` | CSV 読み込み・LLM 設定（全章共通） |

LLM の出力は実行のたびに変わります。回答の文面は本書の例と一致しません。
