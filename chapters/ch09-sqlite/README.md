# 第9章 CSVからSQLiteへ移行する

売上データの置き場を、CSV から SQLite へ移します。Agent から見た Tool の形（入力・出力）は変えません。変わるのは、集計の実装だけです。

| | 第8章まで | 第9章 |
|---|---|---|
| 正本 | CSV（毎回全件を読んで pandas で集計） | SQLite（`data/sales.db`）。CSV は初期データ |
| 集計 | pandas | SQL（目的ごとに固定したクエリ） |
| 入力の扱い | — | 値はパラメータ。列名は許可リスト |
| 接続 | — | 読み取り専用 |
| 不変条件 | コードで検査 | 取り込み時の検査 + DB の制約（キャンセルは売上0など） |

## 使い方

```bash
uv run python chapters/ch09-sqlite/import_csv.py          # CSV を検証して data/sales.db を作る（作り直す）
uv run python chapters/ch09-sqlite/injection_demo.py      # 文字列連結とパラメータ化の違い（一時DBだけを使う）
```

DB がなければ、最初の利用時に自動で取り込みます。以後は DB が正本で、CSV を更新しても自動では反映されません。反映するには、上の取り込みを再実行します。

取り込みに失敗した場合（列の違い・重複・想定外の値・不変条件の違反）は、理由を表示して終了し、DB は変更しません。

各章のアプリ（第4章〜第8章）は、そのまま動きます。

```bash
uv run streamlit run chapters/ch08-human-feedback/app.py
```

## 確認

```bash
uv run pytest tests/unit                       # 取り込み・クエリ・Tool の出力の同一性（LLMを呼ばない）
uv run pytest tests                            # LLMを使う統合テストも含む（APIキーが必要）
uv run python examples/spike/s16_scale.py      # CSV と SQLite の時間を、300件と30万件で比べる（本文の実測）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `import_csv.py` | CSV を検証して取り込む CLI |
| `injection_demo.py` | SQL インジェクションのデモ（教材用） |
| `../../shared/sales_db.py` | スキーマ・取り込み・検証・`SalesRepository`（目的限定のクエリ） |
| `../../tests/golden/sales_tools_csv.json` | 移行前（CSV）の Tool の出力。移行後と一字一句同じであることを確かめる |
| `../../tests/conftest.py` | テストは一時DBを使い、`data/sales.db` に触れない |

## 注意

- 取り込み・参照は SQLite 標準ライブラリです。追加の依存はありません。
- 第10章で、複数のテーブルを安全に扱うために、この Repository を拡張します。
