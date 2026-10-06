# 共通 Business Rule

作成日：2026年10月2日（金）

6 Framework 共通マスター §3.4 に基づく。Framework 非依存で、全章・全実装が守る。
Business Rule と Evaluation Case は Framework 依存にしない（NFR-01）。

## 1. データ

- ファイル：`data/ai_agent_practice_sales.csv`（300件、UTF-8 BOM 付き。読み込みは `utf-8-sig`）
- 期間：2026-01-01 〜 2026-09-16
- 主キー：`order_id`（一意）

### 列

| 列 | 意味 |
|---|---|
| order_id | 注文ID |
| order_date | 注文日 |
| customer_id / customer_name / customer_type | 顧客（customer_type：法人 / 個人） |
| product_id / product_name / category | 商品（category：IT機器 / 周辺機器 / オフィス家具 / ストレージ） |
| unit_price / quantity / discount_rate | 単価・数量・割引率 |
| subtotal | 割引前金額 |
| total_amount | 注文金額（キャンセル注文も含む） |
| sales_amount | **実売上金額**（キャンセルは 0） |
| region | 地域（関東 / 関西 / 中部 / 東北 / 九州） |
| salesperson | 担当者 |
| status | 注文状態（完了 / 処理中 / キャンセル / 保留） |
| review_flag | 通常 / 要レビュー |

## 2. ルール

| ID | ルール | 理由 / 備考 |
|---|---|---|
| BR-01 | 売上集計には `sales_amount` を使う | `total_amount` はキャンセル注文を含み、売上を過大にする |
| BR-02 | キャンセル注文は実売上に含めない | データ上は `status = キャンセル` の `sales_amount` が 0。`sales_amount` を使えば自動的に満たされる |
| BR-03 | LLM に数値集計を任せない | 集計は Python / SQL / Tool へ委譲し、LLM は質問理解・Tool 選択・結果の説明を担う |
| BR-04 | 任意SQLを無制限に許可しない | 目的限定 Tool と Allowed Join で制御する |
| BR-05 | Read-only Tool と Side Effect 処理を分ける | 状態を変更する処理は別 Tool として定義する |
| BR-06 | High Risk な処理には Human Approval を入れる | 例：レビュー依頼の登録。AI の提案と人の承認を分ける |
| BR-07 | Business Data は Database を正本とする | 変化する Business Fact は Memory へ固定せず、毎回再計算する |
| BR-08 | Agent Memory と Business Database を分ける | Memory に残してよいのは Preference / 会話文脈など。売上数値は残さない |
| BR-09 | Production では Logs / Traces / Metrics / Evaluation を分けて扱う | それぞれ目的が異なる |
| BR-10 | Secret・個人情報を Log / Trace / Memory へ出さない | API Key、Password、顧客情報など |

## 3. 検証用の期待値（`sales_amount` 基準）

評価・テストで使う正解値。CSV が変わった場合は再計算する。

| 項目 | 値 |
|---|---:|
| 総レコード数 | 300 |
| 売上合計（`sales_amount`） | 28,298,695 円 |
| 参考：`total_amount` 合計（**誤り**。BR-01 違反の検出用） | 30,500,770 円 |
| キャンセル件数 | 14 |
| 要レビュー件数 | 37 |

### 地域別売上

| 地域 | 売上 |
|---|---:|
| 東北 | 8,439,765 |
| 関西 | 8,373,825 |
| 九州 | 4,980,410 |
| 関東 | 3,268,525 |
| 中部 | 3,236,170 |

最高は **東北**、2位は関西（差は約6.6万円）。

### 顧客種別別売上

| 顧客種別 | 売上 |
|---|---:|
| 法人 | 24,256,195 |
| 個人 | 4,042,500 |

### カテゴリ別売上

| カテゴリ | 売上 |
|---|---:|
| IT機器 | 11,651,680 |
| オフィス家具 | 11,329,340 |
| 周辺機器 | 2,879,995 |
| ストレージ | 2,437,680 |

### その他

- `O0001` は実在し、`review_flag = 通常`、`status = 完了`（Evaluation Case 5 の対象）
- 注意：Zenn 原稿の「関東が最も高い」という回答例は説明用の例であり、実データの最高地域は東北である。本書では実データの値を使う。
