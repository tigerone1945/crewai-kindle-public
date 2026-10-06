# Evaluation 仕様

作成日：2026年10月2日（金）

共通 Evaluation Case（`evaluation_cases.json`）の読み方と、評価指標の定義。
共通マスター §10, §11 に基づく。Case は Framework 依存にしない（NFR-01, NFR-06）。

## 1. Case の構成

| 項目 | 意味 |
|---|---|
| `id` / `title` | Case の識別子・名称 |
| `turns` | ユーザー発話。複数ある場合は同一会話（同一 Session）で順に投入する |
| `available_from_chapter` | その Case を実行できるようになる章。それ以前の章では対象外（スキップ） |
| `criteria` | 評価観点（§2） |
| `expected` | 機械的に判定できる期待値（§3） |
| `scenarios` | 条件分岐する Case（例：承認 / 却下） |

## 2. 共通評価指標

共通マスター §11 の12指標。

| 指標 | 意味 | 主な判定方法 |
|---|---|---|
| Tool Selection | 正しい Tool を選んだか | Code（呼び出し履歴） |
| Data Groundedness | Tool 結果にない数値を作っていないか | Code（数値の突合） |
| Structured Output Validity | 出力が Schema に適合するか | Code（Pydantic 検証） |
| Completeness | 質問に必要な情報を含むか | Code（必須語・必須数値）＋ LLM |
| Business Usefulness | 次のアクションが業務上有用か | LLM-as-a-Judge |
| Safety | 禁止された処理を実行していないか | Code ＋ LLM |
| HITL Compliance | Side Effect の前に承認を得たか | Code（実行順序） |
| Join Safety | 許可された Join だけを使ったか | Code |
| Agent Routing | 適切な Role / Agent に振り分けたか | Code（呼び出し履歴） |
| Error Handling | 失敗時に安全に振る舞うか | Code |
| Latency | 実行時間 | 計測 |
| Cost | Token・実行コスト | 計測 |

## 3. `expected` のキー

| キー | 意味 |
|---|---|
| `tools_any_of` | 少なくとも1つが呼ばれること |
| `tools_forbidden` | 呼ばれてはならない Tool（`"*"` は全 Tool、`*word*` は部分一致） |
| `must_contain_text` | 最終回答に含まれるべき語 |
| `must_contain_numbers` | 最終回答に含まれるべき数値（桁区切り・単位の差は正規化して比較） |
| `must_not_contain_numbers` | 含まれてはならない数値（BR-01 違反の検出など） |
| `final_turn_must_contain_text` | 複数ターンの最終ターンに含まれるべき語 |
| `must_be_refused` | 依頼を拒否・停止すること |
| `requires_approval_before` | 実行前に人の承認が必要な Tool |
| `roles_invoked` | 呼び出されるべき Role（Tool を実際に実行したことで判定する。委譲の記録だけでは不十分：Spike s09） |
| `insight_must_not_introduce_new_numbers` | 入力にない数値を作らない。**比率・割合（例：11.5%）も対象**とし、4桁以上の数値だけで判定しない（Spike s08） |

期待値の根拠は `shared/sales_rules.md` §3。CSV を更新したら両方を更新する。

## 4. 判定の方針

- **Code で決定的に判定できるものは Code で判定する。** 意味・品質・有用性だけを LLM-as-a-Judge に任せる。
- LLM Judge は同じ入力でも評価がぶれる。Code 判定を優先すると全体が安定する。
- LLM の出力は確率的なため、同一 Case を複数回実行したときの合格率も記録する。
- Case を落とした場合は、原因を Role / Task / Tool / Flow のどこにあるかへ切り分けてから修正する（Prompt 修正だけで終わらせない）。

## 5. CrewAI 固有の評価指標

共通指標に追加する。Framework 固有指標は追加してよい（共通マスター §11）。

| 指標 | 意味 | 主な対象 Case | 実施章 |
|---|---|---|---|
| Role Assignment Accuracy | 適切な Role に仕事が割り当てられたか | 07 | 11, 18 |
| Task Completion | 各 Task が期待した出力を完了したか | 全 Case | 18 |
| Delegation Quality | 委譲が必要な場面で適切に委譲したか | 07 | 12, 18 |
| Crew Coordination | Role 間で情報が正しく受け渡されたか | 07 | 12, 18 |
| Flow Branch Accuracy | Flow の分岐が条件どおりに進んだか | 04, 05 | 13, 18 |
| Human Gate Compliance | Human Gate を必ず通ったか | 05 | 8, 13, 18 |
| Memory Relevance | Memory から取り出した内容が文脈に適切か | c01（03 は補助） | 5, 18 |

## 6. Case × 章 マトリクス

| Case | 内容 | 実行可能になる章 |
|---|---|---|
| 01 | 売上全体 | 2 |
| 02 | 地域別 | 2 |
| 03 | Follow-up | 5 |
| 04 | 範囲外 | 7 |
| 05 | Side Effect + Approval | 8 |
| 06 | Multi-table | 10 |
| 07 | Multi-Agent | 11 |
| c01 | Preference を別Sessionで引き継ぐ（CrewAI固有） | 5 |
| c02 | Business Fact を Memory に保存しない（CrewAI固有） | 5 |

CrewAI固有 Case は `crewai_specific_cases.json` に分けて管理する。共通 Case は他 Framework との比較可能性のため、共通マスターと同期する。

各章の Verification では、その章で実行可能な Case を回す。第18章でまとめて実行し、Regression の基準にする。

## 7. Failure Case の追加ルール

本番や開発中に見つけた失敗は Case へ戻す（Production Improvement Loop）。

- ID は `case-XX-...` の連番で追加する。
- 失敗を再現する最小の `turns` と `expected` を書く。
- `notes` に、見つかった経緯と原因の層（Role / Task / Tool / Flow）を記録する。
- 共通 Case と CrewAI 固有 Case は区別して管理する（共通 Case は他 Framework との比較可能性を保つため、変更時は共通マスター側と同期する）。

## 8. 未決事項

- Case の実行ランナーは第18章で実装する。章ごとの Verification では、当面は手動実行または簡易スクリプトを使う。
- LLM-as-a-Judge に使うモデルと採点基準は、Technical Spike 後に決める。
