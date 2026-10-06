# 第10章 Multi-tableとJoin Validationを実装する

複数の表にまたがる集計を、安全に行います。Agent に SQL を書かせません。分析の軸（Dimension）と、許可された結合（Allowed Join）を、System 側のコードで宣言します。

```text
orders（事実表）──┬─ customer_id → customers（customer_id が主キー）  … 許可
                  └─ product_id  → products （product_id が主キー）   … 許可
```

| 軸（Dimension） | 表 | 結合 |
|---|---|---|
| region | orders | 不要 |
| category / product_name | products | orders → products |
| customer_type | customers | orders → customers |

`customer_name`（氏名）・`customer_id`・`salesperson` は軸に載せません。載せたものだけが、Agent から見えます。

## Join Validation（二重）

| 検査 | 内容 | 検出するもの |
|---|---|---|
| 静的 | 結合先のキーが主キー／一意か（`validate_edge`） | Many-to-Many（行が増える） |
| 実行時 | 結合して集計した合計・件数が、結合前（`orders` だけ）と一致するか（`validate_totals`） | 行が増えた・減った |

一致しなければ、数値を返さず `JoinValidationError` にします。

## 使い方

```bash
uv run python chapters/ch10-multi-table/join_demo.py   # Preview・Many-to-Many の水増し（5.0倍）・Validation による停止
```

追加した Tool: `get_sales_by_customer_type`（Case 06）、`get_sales_by_product`。どちらも軸の名前を渡すだけで、結合は Repository が許可リストから決めます。

DB は、`import_csv` で作り直せば、3表の構成になります。第9章で作った古い `data/sales.db` が残っている場合は、スキーマのバージョンの検査で止まり、再取り込みを案内します（自動では作り直しません）。

```bash
uv run python chapters/ch09-sqlite/import_csv.py
```

互換のため、元の18列を返す VIEW `sales_orders` を残しています（`get_order`・`to_dataframe` など）。

## 確認

```bash
uv run pytest tests/unit                                       # データモデル・許可された結合・Join Validation（LLMを呼ばない）
uv run pytest tests/integration/test_ch10_multitable.py        # Case 06（APIキーが必要）
uv run python examples/spike/s18_text_to_sql.py 5              # Text-to-SQL との比較実験（APIキーが必要。一時DBだけを使う）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `join_demo.py` | Preview・水増し・Join Validation のデモ（教材用） |
| `../../shared/join_policy.py` | `DIMENSIONS`・`ALLOWED_JOINS`・`plan_for`・`validate_edge`・`validate_totals` |
| `../../shared/sales_db.py` | 3表のスキーマ、外部キー、互換 VIEW、`SalesRepository.sales_by` / `preview` |
| `../../shared/sales_tools.py` | `get_sales_by_customer_type`・`get_sales_by_product` |

## 注意

- 新しい分析軸を足すには、`DIMENSIONS`（と、必要なら `ALLOWED_JOINS`）にコードで宣言します。レビューを通る変更になります。
- 担当者別の売上は、いまは軸にありません（従業員名のため）。必要になったときに、宣言して足します。
