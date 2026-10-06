# 第11章 RoleでAgent Responsibilityを分ける

1人の Agent に集まっていた責務を、3つの Role に分けます。分けると品質が上がるのか、コストはどれだけ増えるのかを、測って確かめます。

| Role | 責務 | 持つ Tool | 持たないもの |
|---|---|---|---|
| データ品質担当 | 分析の前に、データの品質を事実で報告する | `check_data_quality` | 売上の分析・示唆 |
| 売上分析担当 | Tool の結果だけで、売上の傾向を数値で報告する | 売上の集計 Tool（5つ） | 業務上の解釈・提言 |
| 業務示唆担当 | 前工程の結果から、示唆を整理する | なし | Tool。**文章に数値を書くこと**（数値は `metrics` にだけ） |

Role の分け方は、Tool（権限）の境界に合わせます。Tool を持たない Role は、データを直接読めません（Context Boundary）。Task は Role ごとに1つで、前工程の出力を `context` で渡します。

## 使い方

```python
import shared.analysis_service as service

result = service.run_multi_role_analysis("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")
```

入力の検査（第7章）と、出力の検査（最終の Task の Guardrail）は、単一の Agent と同じです。Role を分けても、安全の境界は変わりません。画面への組み込みと、質問の振り分けは、第13章（Flow）で行います。

## 確認

```bash
uv run pytest tests/unit                                  # Role・Task・権限境界・Case 07 の判定（LLMを呼ばない）
uv run pytest tests/integration/test_ch11_roles.py        # Case 07（APIキーが必要）
uv run python examples/spike/s19_roles_vs_single.py 6     # 単一 Agent と 3 Role の比較（APIキーが必要。引数: 回数、both/single/roles）
```

## 実測（各6回、gpt-4o-mini）

| | 成功 | 1回で合格 | 平均 Token | 平均時間 |
|---|---:|---:|---:|---:|
| 単一 Agent | 6/6 | 6/6 | 5,475 | 9.2 秒 |
| 3 Role（責務の設計が悪い） | 4/6 | 0/6 | 16,591 | 45.5 秒 |
| 3 Role（責務を直した） | 6/6 | 6/6 | 6,557（1.2倍） | 17.8 秒（1.9倍） |

## ファイル

| ファイル | 内容 |
|---|---|
| `../../shared/roles.py` | 3つの Role（Agent）、Task、`check_data_quality`、権限境界の検査 |
| `../../shared/analysis_service.py` | `run_multi_role_analysis`（単一 Agent と共通の、検査つき実行） |
| `../../shared/sales_db.py` | `SalesRepository.data_quality()` |
| `../../shared/tracing.py` | 動いた Role と、Tool を実行した Role の記録 |
| `../../evaluations/checks.py` | `roles_invoked`・`must_contain_numbers_any_of` の判定 |
