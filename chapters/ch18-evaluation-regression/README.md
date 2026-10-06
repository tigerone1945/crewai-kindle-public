# 第18章 EvaluationとRegressionで改善Loopを作る

観測できること（第17章）と、品質を判定できることは別です。この章では、これまで各章で手動・簡易に確かめてきた Case を、**1つのランナー**にまとめ、基準と比べて、デプロイしてよいかを決めます。見つけた失敗は、Case に戻します。

| | 内容 |
|---|---|
| Case | 共通 7 + CrewAI 固有 4（Memory 2・Flow の分岐・Human Gate）+ 失敗 Case 4（`evaluations/*.json`）。計 15 |
| ランナー | 本番と同じ入口（Flow）から実行。LLM を呼ばない Case は1回、呼ぶ Case は複数回 |
| 採点 | Code で決定的に。評価軸ごと（Tool Selection / Groundedness / Structured Output / Completeness / Safety / HITL / Join Safety / Role Assignment / Delegation / Crew Coordination / Flow Branch / Human Gate / Memory Relevance / Memory Policy / Tool Economy / Context Carry-over / Task Completion） |
| 判定（Regression） | Case ごとの基準 `min_pass_rate`。合格率の 95% 信頼区間（Wilson）の上限が基準未満なら FAIL、見かけだけなら WARN。決定的な Case は1回の失敗で FAIL。Token・時間の変化も見る |
| 失敗の Case 化 | `add_failure_case.py`。秘密情報・個人情報・採点されないキー・空の `expected` を拒否 |
| 審査員 | Business Usefulness を LLM が採点。**参考値**（合否に使わない） |

## 使い方

```bash
# すべての Case を、3回ずつ（LLM を呼ぶ。数分）。基準と比べる
uv run python chapters/ch18-evaluation-regression/run_evaluation.py --runs 3

# デプロイの前: 基準を下回れば、終了コード 1
uv run python chapters/ch18-evaluation-regression/run_evaluation.py --runs 10 --gate

# LLM を呼ばない Case だけ（数秒）
uv run python chapters/ch18-evaluation-regression/run_evaluation.py --cases case-04-out-of-scope,case-05-side-effect-approval,case-c03-flow-branch,case-c04-human-gate

# 基準の更新（測った結果を、人が確かめてから）。min_pass_rate は引き継ぐ
uv run python chapters/ch18-evaluation-regression/run_evaluation.py --runs 5 --update-baseline

# 見つけた失敗を Case にする（質問は、再現できた文面を、人が書く）
uv run python chapters/ch18-evaluation-regression/add_failure_case.py add --slug <名前> --title "..." --layer tool \
    --question "..." --expected '{"tools_any_of": ["get_sales_summary"], "max_tool_calls": 2}' --notes "..."
```

## Production Improvement Loop

```text
Production → Observe（第17章）→ Failure Detection → 層の切り分け（Trace の最初の error）
  → Evaluation → Failure Case 化 → 修正（Prompt だけでなく Role / Task / Tool / Flow のどこか）→ Regression → Deploy
```

この章の実例: 共通 Case 01（売上全体）が、時々失敗した。Trace で、Tool を1回だけ呼んだ実行は 10 回中 8 回すべて成功。全 Tool を 10〜15 回呼び歩いた 2 回のうち、1 回が失敗した。原因の層は Tool の選択（Tool の説明）。説明を直し、20 回ずつ測った（下表）。

## 実機で確かめたこと

| | 結果 |
|---|---|
| 基準（5回ずつ、`gpt-4o-mini`） | 15 Case すべて合格。1 回の分析は 約 3,500〜7,700 Token（Multi-Agent・Follow-up が高い） |
| **失敗 F-002（売上全体）** | 修正前 14/20 合格（5 回は回答できず、6 回は Tool を 3 回超呼んだ）、平均 7,795 Token。説明を直して 20/20、3,574 Token（片側 Fisher 検定 p=0.010） |
| 劣化の注入（ゲートは見つけるか） | 下表 |
| ゲートの検出力 | 基準 0.8 に対し、真の合格率 0.6 の Case を見つける確率は、5 回で 32%、10 回で 37%、20 回で 58%。0.2 なら 94%・99%・100%。基準ちょうどの Case を誤って止める確率は 6%・3%・3% |
| 審査員 | 同じ回答を 5 回採点して、採点は毎回同じ（幅 0）。一般的な提案への劣化は見分ける。**要約と数値の矛盾は、見分けられない** |

| 注入した劣化 | 結果 |
|---|---|
| Tool の出力から「上位からの累積」を消す（第12章の修正を戻す） | F-001 地域 3/5（WARN: 回数が少ない）、カテゴリ 0/5（**FAIL**）、商品 5/5（影響なし）。Token 2.7 倍 |
| 経路の規則から「品質」を外す | `flow_branch` を **FAIL**（決定的）、Multi-Agent 0/5（**FAIL**） |
| `get_sales_by_region` の説明を、無関係な文に差し替える | **検出できなかった**（5/5）。Tool の名前だけで選べてしまう。説明の劣化は、曖昧な質問の Case でしか見つからない（F-002 のように） |

## 限界

- LLM の採点（審査員）は、参考値。矛盾を見分けられない。Business Usefulness を、合否の基準にできていない
- 合格率の基準（0.8）は、人が決める値。Case ごとの最適値は、この章では、測っていない
- 数回の実行では、中程度の劣化は、見逃しうる（上の検出力）。回数を増やすか、Case を増やす
- Case は、書いた範囲の劣化しか見つけない（説明の劣化は、検出できなかった）
- モデルは `gpt-4o-mini` の 1 つ。モデルを変えたときの基準は、測り直す。出版時点で再確認する
- 実行は、手元の 1 台。CI への組み込み（`pytest tests/evaluations`）は、仕組みだけ。実際の CI では動かしていない

## 確認

```bash
uv run pytest tests/unit tests/regression            # 採点・判定・失敗 Case 化・LLM を呼ばない Case（数秒）
uv run pytest tests/evaluations                      # すべての Case を本物で（APIキー。EVAL_RUNS で回数）
uv run python examples/spike/s26_regression_gate.py ABC 5   # 検出力・劣化の注入・審査員（本文の実測）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `evaluations/evaluation_cases.json` | 共通 Case（他 Framework と比較可能。変更時は共通マスターと同期） |
| `evaluations/crewai_specific_cases.json` | CrewAI 固有 Case（Memory・Flow の分岐・Human Gate） |
| `evaluations/failure_cases.json` | 見つけた失敗の Case |
| `evaluations/runner.py` / `grading.py` / `gate.py` / `judge.py` | 実行 / 採点 / 判定 / 審査員 |
| `evaluations/failure_cases.py` | 失敗の Case 化の検査 |
| `evaluations/baseline.json` | 基準（測った合格率・`min_pass_rate`・Token・時間） |
