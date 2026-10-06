# 第8章 FlowへHuman Feedbackを組み込む

状態を変える処理（レビュー依頼の登録）の前に、人の承認を挟みます。Agent には書き込みの Tool を渡さず、Flow が、承認を得たあとにだけ実行します。

| 分類 | 例 | 扱い |
|---|---|---|
| Read-only | 売上の集計 Tool | Agent が自由に呼んでよい |
| Write（低リスク・元に戻せる） | 好みの登録（利用者本人の操作） | 利用者本人が行う。Agent は行わない |
| High Risk | レビュー依頼の登録 | 人の承認が必要（Flow が実行） |

```text
prepare → gate ─ invalid ─────────────────────────→ fail
                └ needs_approval → request_approval（一時停止）
                                      → decide ─ approved → execute
                                                └ rejected → cancel
```

承認の判断は LLM に任せません。`@human_feedback` に `emit` を渡すと、LLM が返答を分類します。そこで `emit` を使わず、`@router` が完全一致（`approve`）だけを承認とみなします。それ以外はすべて却下です。

## 起動

```bash
uv run streamlit run chapters/ch08-human-feedback/app.py
```

1. 「O0001をレビュー対象として登録してください。」と入力 → 「承認待ち」になる（この時点では登録されない）
2. 承認者IDを `approver-001` のまま（依頼者は `user-001`）で「承認」→ 登録される。「却下」なら何も登録されない
3. 依頼者と同じIDでは承認できない（職務の分離）
4. 承認待ちはファイルに保存されている。画面を再読み込み（アプリを再起動）しても、承認・却下できる

保存先: `data/review.db`（依頼と監査ログ）、`data/flows.db`（Flow の状態）。どちらも Git の対象外です。

## 確認

```bash
uv run pytest tests/unit    # 承認Flow・画面・Case 05（LLMを呼ばない。毎回同じ結果）
uv run python examples/spike/s15b_flow_router.py   # emit なしの承認判断を、実際のFlowで確かめる
```

## ファイル

| ファイル | 内容 |
|---|---|
| `app.py` | 第7章の画面に、承認待ちの一覧と承認・却下を追加 |
| `../../shared/review_flow.py` | 承認 Flow（`ReviewFlow`）、`start_review`、`resolve_review` |
| `../../shared/review_requests.py` | レビュー依頼の保存（High Risk な書き込み）と監査ログ |
| `../../shared/tracing.py` | Flow のステップを Trace に記録 |
| `../../docs/figures/ch08-pending-approval.jpg` | 実行画面（本文の図） |

## 注意

- 承認者IDは、画面の入力欄に打った文字列です。認証ではありません。本番では認証・認可で決めます（第16章）。
- 「登録」は、レビュー依頼の記録を作るだけです。CSV の `review_flag` は変わりません（第9章でDatabaseへ移します）。
- Flow は、メソッドの実行状況をコンソールにパネルで表示します（メソッド名と ID のみ。内容は出ません）。
