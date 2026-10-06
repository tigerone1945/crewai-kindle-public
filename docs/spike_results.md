# Technical Spike 結果

実施日：2026年10月2日（金）
環境：CrewAI 1.15.23 / Python 3.13 / モデル `openai/gpt-4o-mini`（temperature=0）
コード：`examples/spike/`（`common.py`, `s01`〜`s03`）

## 進捗

| Spike | 対象 | 章 | 状態 |
|---|---|---|---|
| s01 | Agent / Task / Crew | 1 | 確認済み |
| s02 | Tool（Agent単位・Task単位） | 2 | 確認済み |
| s03 | Structured Output | 3 | 確認済み |
| s04 | Flow + Human Feedback（承認 / 却下） | 8 | 確認済み |
| s05 / s05b | Memory と Follow-up | 5 | 確認済み（Case 03 に課題あり） |
| s06 | Task Guardrail | 7 | 確認済み |
| s07 | Event Bus による実行追跡 | 6 | 確認済み |
| s08 | 3 Role の Crew（Sequential）と単一 Agent の比較 | 11, 12 | 確認済み |
| s09 | Hierarchical Process と Delegation | 12 | 確認済み |
| s10 | Flow が Crew を呼ぶ（State / 分岐 / Retry / fallback / 入力境界） | 13 | 確認済み |
| s11 | Trace 用 Event の順序・ContextVar の伝播・Event が運ぶ内容 | 6 | 確認済み |
| s12 | 所要時間の内訳（5回） | 6 | 確認済み |
| s13 | Task Guardrail と output_pydantic の併用、ガードレールなしの基準値 | 7 | 確認済み |
| s14 | Guardrail の再生成頻度・コスト、言い換えインジェクション、範囲外の止め方 | 7 | 確認済み |
| s15 / s15b | Flow の一時停止・再開（Human Feedback）、emit の LLM 分類と、emit なしの決定論的な承認 | 8 | 確認済み |
| s17 | Task Guardrail の再生成方式の比較（失敗しやすい質問） | 7（9章で訂正） | 確認済み |
| s16 | CSV(pandas) と SQLite の集計・1件検索の時間（300件 / 30万件） | 9 | 確認済み |
| s18 | Text-to-SQL（自由な SQL）の比較実験 | 10 | 確認済み |
| s19 | 単一 Agent と 3 Role の比較（Case 07、第11章の実装で再測定） | 11 | 確認済み |
| — | Production（AMP）| 16 | 一部（ドキュメントと検査コマンドのみ。配置はしていない）|
| — | MCP | 付録K | 未実施 |

## 確認できたAPI

| 項目 | 確認結果 |
|---|---|
| Agent | `Agent(role, goal, backstory, tools, llm)`。Instructions に相当するのは role / goal / backstory |
| Task | `Task(description, expected_output, agent, tools, output_pydantic)` |
| Crew | `Crew(agents, tasks, process)`。`Process.sequential` を指定可能 |
| 実行 | `crew.kickoff()` が `CrewOutput` を返す。`.raw`（文字列）、`.pydantic`、`.token_usage`、`.tasks_output` |
| Tool | `from crewai.tools import tool`。`@tool("名前")` で関数を Tool 化。docstring が説明になる。型は `Tool` |
| Tool の指定場所 | **Agent の `tools=` と Task の `tools=` の両方で動作**（Agent 単位 / Task 単位） |
| Structured Output | `Task(output_pydantic=Model)`。`out.pydantic` と `out.tasks_output[0].pydantic` に型付きで入る。`out.raw` は文字列のまま |
| 入力の埋め込み | `Task.description` に Dataset Profile を直接埋め込んで動作 |
| 実装上のメモ | Agent / Task / Crew に `memory` / `guardrail` / `human_input` / `mcps` 等の属性が存在する（第5章以降の Spike で挙動を確認する） |

## 確認できたAPI（第5〜8章）

| 項目 | 確認結果 |
|---|---|
| Flow | `from crewai.flow import Flow, start, listen, human_feedback`。`class X(Flow[State])` で Pydantic の State を持つ。`kickoff()` の戻り値は最後に実行されたメソッドの戻り値 |
| Human Feedback | `@human_feedback(message, emit=[...], llm, default_outcome, provider)`。人の自由記述を LLM が `emit` の選択肢のいずれかへ分類（collapse）し、`@listen("approved")` のように分岐できる。結果は `flow.last_human_feedback`（`.output` / `.feedback` / `.outcome`） |
| Provider | `HumanFeedbackProvider.request_feedback(context, flow) -> str` を実装すると、入力手段を差し替えられる（Console / Slack / Web 等）。非同期は `HumanFeedbackPending` を送出して Pause する設計 |
| Memory | `Memory(llm=, storage=)`。既定は LanceDB 保存。`remember(content, scope=)` / `recall(query, scope=, limit=)`。Scope は `/user-001/preferences` のような階層。`Crew(memory=...)` / `Agent(memory=...)` で有効化 |
| Guardrail | `Task(guardrail=関数, guardrail_max_retries=n)`。関数は `(TaskOutput) -> (bool, 結果またはエラー文)`。失敗するとエラー文が Agent へ戻されて**自動リトライ**される。文字列を渡すと LLM 判定になる型もある |
| Event | `BaseEventListener` + `crewai_event_bus`。`TaskStartedEvent` / `ToolUsageStartedEvent` / `ToolUsageFinishedEvent` / `TaskCompletedEvent` 等で実行過程を取得できる |
| MCP | Agent に `mcps` 属性が存在する（付録K。挙動は未検証） |

## 確認できたAPI（第11〜13章）

| 項目 | 確認結果 |
|---|---|
| Role | Agent ごとに `role / goal / backstory / tools / allow_delegation` を持つ。Tool は Role ごとに分けて渡せる |
| Task の受け渡し | `Task(context=[前のTask])` で前工程の出力を次の Task に渡す。Task ごとに `agent` を割り当てる |
| Sequential | `Crew(process=Process.sequential)`。Task の順に実行。実行履歴は `out.tasks_output`（各 Task の担当 Agent と出力） |
| Hierarchical | `Crew(process=Process.hierarchical, manager_llm=...)`。Task に Agent を割り当てず、Manager が委譲する。委譲は `ask_question_to_coworker` 等の Tool として現れ、`task.delegations` に件数が入る |
| Flow の分岐 | `@router(前のメソッド)` が返した文字列ラベルを `@listen("ラベル")` が受ける |
| Flow の合流・Retry | `@listen(or_("in_scope", "retry"))` のように複数ラベルで同じメソッドを再実行できる（ループ可）。試行回数は State（`self.state.attempts`）で管理 |
| Flow の入力 | `flow.kickoff(inputs={"question": ...})` で State を初期化する。結果は `flow.state` で参照 |
| Crew の呼び出し | Flow のメソッド内で `Crew(...).kickoff()` を呼ぶだけでよい |

## 検証結果（共通 Evaluation Case との対応）

| Case | 結果 |
|---|---|
| 01 売上全体 | `get_sales_summary` を呼び、28,298,695円を含む。`total_amount` 合計は出ない。合格 |
| 02 地域別 | `get_sales_by_region` を呼び、5地域の数値が期待値と一致。合格 |
| 03 Follow-up | 「では一番高かった地域は？」に東北と回答。合格。**ただし Memory なしでも合格しており、Memory の効果を判別できていない（後述）** |
| 04 範囲外 | backstory の指示だけで拒否し、Tool も呼ばなかった。合格。ただしこれは Instruction（お願い）による拒否で、境界ではない |
| 07 Multi-Agent | **Sequential：Role 分離は成功**（各 Tool は担当 Role のみが使用）。**Hierarchical：失敗**（データ品質担当が一度も Tool を使わず、品質確認が一般論の作文になった）。詳細は発見10〜13 |
| 04 範囲外（Flow 版） | Flow の入力境界で拒否し、LLM を一度も呼ばなかった（s10）。合格 |
| 05 Side Effect + Approval | 承認 → 実行、却下 → 未実行、曖昧な返答 → 未実行（`default_outcome="rejected"`）。3シナリオとも期待どおり。合格 |

## 本文に使える発見

1. **Dataset Profile だけでは LLM が実データを推測する**（s01）
   顧客タイプを「新規顧客、リピーター」と例示した。実データは法人 / 個人。第1章で「概要だけでは実データの値を知らない」例として使える。第2章で Tool が必要になる動機になる。
2. **Tool 結果が正しくても、LLM の解釈は誤りうる**（s03）
   Tool は正しい値を返したが、`key_findings` に「関東地域の売上は3,268,525円で最も低い」とあった。実際の最低は中部（3,236,170円）。差は約3万円と僅差だった。Structured Output で形式は守られても、内容の正しさは保証されない。Data Groundedness の評価（第18章）と、数値の突合を Code で行う設計の動機になる。
3. **Tool の指定場所は Agent / Task の両方にある**（s02）
   Agent に持たせると全 Task で使える。Task に持たせるとその仕事だけに限定できる。Role 分割（第11章）で、権限境界として使い分ける論点になる。
4. **Structured Output は Task の契約**（s03）
   OpenAI 版の `output_type`（Agent 側）とは指定場所が異なる。同じ Agent でも Task ごとに異なる出力形式を返せる。

5. **Human Feedback は「承認・却下」を自由記述から分類する**（s04）
   `emit` で選択肢を定義し、人の自然な返答を LLM が分類する。曖昧な返答は `default_outcome` で安全側（却下）へ倒せる。ただし分類は LLM が行うため、Side Effect を伴う場面では「分類が誤る可能性」を設計に織り込む必要がある（明示的なボタン入力などの併用を検討）。
6. **Guardrail の失敗は Agent へのフィードバックになる**（s06）
   失敗時のエラー文が Agent へ返され、再試行される。`2 合計を求める` では、LLM が自分で足し算した合計（28,298,695。値は正しい）を guardrail が「Toolの結果にない数値」として検出した。BR-03（LLM に集計させない）の違反を機械的に検出できる。一方、**リトライを使い切ると汎用の `Exception` が送出される**。アプリ側で捕捉し、ユーザーへ安全に返す設計が必要（第7章の Failure Handling）。
7. **Guardrail は出力の検証が中心**（s06）
   Task Guardrail は Agent の実行後に出力を検証する。入力が範囲外かどうかを Agent 実行前に止める仕組みではない。Case 04 のような入力境界は、Flow の前段（決定論的な判定）で置くのが自然（第7章・第13章の論点）。
8. **実行追跡は Event で取れる**（s07）
   Task 開始 → Tool 開始 → Tool 終了 → Task 完了が時刻付きで取得できた（例：Task 全体 約2.3秒、Tool 呼び出し 約0.01秒、残りは LLM）。Latency の内訳を見る第6章の題材になる。
9. **Memory の Scope は Actor 分離に使える**（s05）
   `/user-001/preferences` と `/user-002/preferences` は混ざらず、Scope 指定の `recall` はそれぞれの記憶だけを返した。Preference を保存し、Business Fact は保存しない方針（BR-07, BR-08）を、Scope で表現できる。

10. **Role 分離は Sequential で機能する**（s08）
    品質確認Tool は品質担当、集計Tool は分析担当のみが使用した。Case 07 の Role Assignment は合格。
11. **Coordination Cost は大きい**（s08, s09）
    同じ質問に対する実測（各1回、`gpt-4o-mini`）：

    | 構成 | 時間 | トークン | LLM 呼び出し |
    |---|---:|---:|---:|
    | 単一 Agent（3 Tool） | 5.9秒 | 1,239 | 2 |
    | Crew 3 Role（Sequential） | 11.8秒 | 2,171 | 5 |
    | Crew 3 Role（Hierarchical） | 15.6秒 | 5,862 | 6 |

    Sequential は単一 Agent の約1.75倍のトークン・約2倍の時間。Hierarchical は約4.7倍のトークン。Agent を分ける価値は、コストに見合う場合に限られる（第11章の「分けない判断」の根拠）。
12. **Hierarchical は委譲先が仕事をするとは限らない**（s09）
    Manager は3回委譲したが、データ品質担当は一度も Tool を実行せず、最終回答の「品質確認」は実データを見ていない一般論だった。Delegation が「質問」として扱われ、Tool 実行に至らないことがある。確実に実行させたい工程は Sequential（または Flow）で固定する、という本書の原則（Agency と Deterministic Control の分離）を裏づける。1回の実行結果なので、確率的な傾向として扱う。
13. **示唆担当が入力にない数値（比率）を作った**（s08）
    最終回答に「関東の売上は全体の約11.5%」とあった。入力に無い数値で、LLM が計算した（値は正しい）。数値の突合を4桁以上の数値に限ると検出できない。Case 07 の「新しい数値を作らない」判定は、比率・割合も対象にする必要がある（`evaluation_spec.md` に反映）。
14. **Flow で「決める部分」を Code に戻せる**（s10）
    入力境界（Case 04）は Flow の `@router` で決定論的に判定し、範囲外は Crew・LLM を呼ばずに拒否した。Crew の失敗（Guardrail 超過の `Exception`）は Flow 内で捕捉して State に変換し、`@router` で Retry（最大2回）→ fallback（エスカレーション）へ進めた。3経路（正常 / 範囲外 / 失敗）とも設計どおりに動作。Crew = Agency、Flow = Deterministic Backbone を、実装で示せる。

## Evaluation Case 03 の課題（要判断）

- s05 / s05b では、Memory の有無で回答が変わらなかった。「一番高かった地域は？」「その1位の地域の売上は全体の何%？」のような質問は、Tool を再実行すれば解けるため、会話の記憶が不要。
- 売上分析は Tool で再計算できる性質のため、Follow-up Case が Memory を判別できない。Memory の効果は、会話の文脈にしかない情報（例：Preference、直前に指定した分析軸の継承）で測る必要がある。
- 対応（承認済み）：Case 03 は「Context Carry-over」として残し、Memory が必須になる Case を CrewAI 固有 Case として `evaluations/crewai_specific_cases.json` に追加した（c01：Preference の引き継ぎ、c02：Business Fact を保存しない）。
- 検証（s05c, s05d）：
  - Preference（「表形式で見たい」）を別 Crew で引き継げるか。Memory なし＝箇条書き、Memory あり＝表。**Memory の有無で結果が変わる**ことを確認した。
  - **Crew の自動 Memory は、Tool が返した売上数値まで保存した**（8件保存、売上数値を含む）。BR-07 / BR-08 に違反する。
  - 保存を明示的な `remember` のみとし、Crew には `Memory(read_only=True)` を渡す構成にすると、Preference は読めて（表形式で回答）、保存は1件、売上数値は含まれなかった。**Memory Policy は「書き込みを明示的な remember に限定し、Crew は読み取り専用」とする**（第5章の Design Decision）。
- 副次的な観察：「全体の何%」の質問では LLM が自分で割り算をして 29.8% と答えた（値は正しい）。BR-03 の厳密な適用範囲（比率計算を Tool に寄せるか）は、第2章の設計で決める。

### 第6章（s11 / s12）で確認したこと

- Event Handler は別スレッドで動くが、`ContextVar` は引き継がれる（`crewai_event_bus` が `copy_context()` で実行する）。実行ごとの Trace を `ContextVar` で束ねられる。
- 終了 Event は `started_event_id` を持つ。開始 Event と対応づけて所要時間を出せる。
- `flush()` を呼ぶと、非同期で処理中の Event を取り込める。
- **`Task.name` を指定しないと、Event の `task_name` は description（質問文を含む）になる。** Trace に残すときは `name` を指定する。
- Task 系 Event の `agent_role` は空で、Agent 系 Event は `event.agent.role` から取る。
- `verbose=True` のとき、CrewAI のコンソール出力に Tool の出力が表示される（`console_formatter` のコードと実行結果で確認）。
- Event は、プロンプト全文（`messages`）・Tool の引数と出力・LLM の応答を運ぶ。Trace には許可した項目だけを書く。
- 実測（地域別の質問、5回、gpt-4o-mini）：平均 6,344 ms のうち LLM が 99%（6,306 ms）、Tool は 7 ms。入力 2,710 Token（固定）、出力 414〜477 Token。
- ブラウザの実行で、第3章のやり直しが働いた実行を観測した（試行2回、LLM 4回、13.1秒、5,588/854 Token）。やり直しは利用者にも開発者にも見えなかった。

### 第7章（s13 / s14）で確認したこと

- **Task 生成後に `guardrail` を代入しても、検査は呼ばれない**（0回）。`Task(guardrail=...)` で渡す。
- **`__call__` を持つクラスのインスタンスは Guardrail に渡せない**。CrewAI が `inspect.getsource()` を呼ぶため `TypeError`。関数か束縛メソッドを渡す。
- **`output_pydantic` と併用すると、最初の検査では `output.pydantic` が空**。1回で合格した場合の最終 `CrewOutput.pydantic` も空。`output.raw` から組み立て、検査を通った結果は自前で保持する。
- リトライを使い切ると、`builtins.Exception("Task failed guardrail validation after N retries. Last error: ...")` が送出される（s06 と同じ。メッセージの書き出しを統合テストで固定）。
- ガードレールなしの基準値：「Pythonでゲームを作って」に対して、Tool 3つを呼び、売上サマリを返した（LLM 2回、2,897/430 Token、6.3秒）。`backstory` の「対応するToolがない質問は unsupported」は守られなかった。
- 入力Guardrailで止めると 0ミリ秒・0 Token・LLM 0回。
- 通常質問10回（gpt-4o-mini）：1回で合格8、再生成1回で合格2、使い切り0。再生成した回は LLM 4回・約7,200 Token・11〜14秒（通常は2回・約3,200 Token・5〜8秒）。**再生成の2回とも Tool が呼び直された**（Task 内のやり直しでも Tool は再実行されうる）。
- 言い換えたインジェクション（設定の出力要求）5回：入力Guardrailは通過、設定の漏えいは0回、Agent は要求を無視して売上概要を返した。保証ではない。

### 第8章（s15 / s15b）で確認したこと

- Provider が `HumanFeedbackPending` を送出すると Flow が一時停止し、`kickoff()` は**例外ではなく `HumanFeedbackPending` を返す**。状態は `SQLiteFlowPersistence` に保存される。
- 別のインスタンス（別プロセス相当）で `Flow.from_pending(flow_id, persistence, **kwargs)` → `resume(feedback)` で再開できる。`kwargs` はコンストラクタへ渡る。State も復元される。
- 再開すると保留は消え、**同じ `flow_id` の2回目の再開は `ValueError: No pending feedback found`**。二重実行を Flow が防ぐ。
- **`@human_feedback(emit=[...])` は、返答が `"approved"` と正確に一致しても、LLM を1回呼んで分類する**（空文字のときだけ呼ばず `default_outcome`）。「approved but only if the amount is small」のような条件つきの返答は `approved` に分類され、Side Effect が実行された。承認判断は LLM に任せない。
- **`emit` を使わず `@router` で `last_human_feedback.feedback` を完全一致で判定**すると、LLM 呼び出しは0回で、`"approve"` 以外（空・「はい」・条件つき）はすべて却下になる。
- Flow は、メソッドの開始・完了・一時停止を Event として発行する（Trace に取り込める）。コンソールには、メソッド名と ID だけのパネルが出る（内容は出ない）。
- 再開すると、停止していたメソッド（`request_approval`）の完了 Event が再び発行される。

### 第9章（s16）で確認したこと

- 実測（中央値、5回。M系Mac、`pandas` の CSV 全件読み込み vs SQLite）：

| | CSV（pandas） | SQLite | 備考 |
|---|---:|---:|---|
| 300件・集計（地域別） | 1.34 ms | 0.30 ms | |
| 300件・1件検索 | 1.42 ms | 0.18 ms | |
| 30万件・集計 | 440 ms | 200 ms | 約2.2倍 |
| 30万件・1件検索（主キー） | 437 ms | 0.2 ms | 約2,000倍 |
| 30万件・取り込み | — | 7.5 秒（1回だけ） | CSV 42.7 MB → DB 61.8 MB |

- 現在のデータ（300件）では、性能は移行の理由にならない。移行の理由は、制約・パラメータ化・読み取り専用・更新と同時アクセス・第10章の Join。
- 文字列連結のクエリに `O0001' OR '1'='1` を渡すと全件（300件）が返る。パラメータ化したクエリでは該当なし（`chapters/ch09-sqlite/injection_demo.py`）。
- 識別子（列名）はパラメータにできない。許可した列ごとに SQL を固定し、入力は SQL に入れない。
- `import_csv` の既定引数に `DB_PATH` を直接書くと、定義時の値に固定され、差し替えが効かない（テストで発見。実行時に解決する）。
- SQLite の `CHECK ((status = 'キャンセル') = (sales_amount = 0))` で、BR-02 を DB の制約としても守れる。

### 第7章の訂正（s17。第9章の作業中に発見）

- s14 の「通常質問10回で使い切り0」は、失敗しにくい質問（「…を比較してください」）での結果だった。**「…を分析してください」（好みなし）では、Task 内の再生成（最大2回）が 10回中7回、使い切って失敗した**（合格3/10）。第8章時点の CSV 版コードでも再現した。SQLite 化の影響ではない。
- 原因：Agent が構成比を自分で足した文（「東北と関西で59.4%」）を書き、Task 内の再生成では、理由を伝えても同じ文を繰り返す（直前の誤りが文脈に残る）。
- 比較（各10回、「…を分析してください」）：Task 内再生成のみ 3/10（却下理由に直し方を加えて 5/10）／**作り直しのみ（最大3回）9/10・11.4秒・5,362 Token**／Task 内1回＋作り直し（最大2回）10/10・18.1秒・9,598 Token。
- 結論：Task Guardrail は検査に使い（`guardrail_max_retries=0`）、却下されたら理由を質問に加えて Crew を作り直す。第3章の方式（19/20）と同じ。
- 教訓：再生成の方式は、失敗しやすい質問で測って選ぶ。偽物の Crew の単体テストは通り続けていた。

### 第10章（s18）で確認したこと

- データモデル：顧客10件・商品12件。顧客・商品の属性は ID ごとに一意で、表に分けられる。`unit_price` は商品ごとに値が変わる（注文時点の価格）ため、`products` ではなく `orders` に置く。
- 顧客と担当者は Many-to-Many（1顧客に最大5人の担当者）。対応表をそのまま結合すると、売上は 141,493,475 円（本来 28,298,695 円の **5.0 倍**）、件数は 1,500（本来 300）。
- Join Validation：静的な検査（結合先のキーが一意か）と、実行時の検査（結合後の合計・件数が結合前と一致するか）のどちらでも検出できる。許可リストに誤って加えても、静的な検査で止まる。
- Text-to-SQL（`execute_sql` Tool を持つ Agent。読み取り専用の接続。スキーマに `sales_amount` を使う旨と、顧客ごとの担当者の対応表を記載。gpt-4o-mini、各5回）：

| 質問 | 実行した SQL の結果が正しい | 備考 |
|---|---:|---|
| 顧客種別ごとの売上 | 5/5 | |
| 担当者ごとの売上 | **0/5** | 5/5 が対応表を結合（`orders` に担当者の列があるのに）。水増し |
| 顧客ごとの担当者の情報を使って担当者別 | **0/5** | 5/5 が対応表を結合。水増し |
| 顧客名ごとの売上ランキング | — | **5/5 が氏名をそのまま回答に出した** |

  売上の定義（`sales_amount`、BR-01）は、スキーマに書いておけば 100% 守られた。
- 実験の条件は、対応表をスキーマに載せたもので、水増しを起こしやすい。ただし、実際のスキーマにも、このような表は含まれうる。
- 文面に数値が出ない回答があり、回答の数値での採点は不正確だった。実行した SQL の結果の合計が、売上合計と一致するかで採点した。

### 第10章の作業中に見つかったこと

- 開発用の `data/sales.db` が旧スキーマ（第9章）のまま残ると、`no such table: orders`。→ `PRAGMA user_version` でスキーマのバージョンを検査し、再取り込みを案内する（自動では作り直さない）。
- 旧スキーマでは `sales_orders` は表、新スキーマでは VIEW。再取り込みが `DROP VIEW` で失敗した。→ 種類を調べてから削除。旧スキーマからの移行をテストで保証。
- LLM にタイムアウトがなく、通信が応答なしのまま20分以上止まった。→ `LLM_TIMEOUT_SECONDS`（既定60秒）を設定。
- 「構成比を足す」失敗への対策（s17、「…を分析してください」好みなし、`max_attempts=3`・`task_retries=0`）：Task の指示に具体的な禁止例を加えると、1回で合格 17/20（加える前は 7/10・5/10・3/10。調査の途中の測定で、条件が一部異なる）、全体で 19/20 合格（使い切り 1/20）、平均 9.2 秒・4,375 Token。好み付き（「結論:」）は 10/10 が1回で合格。

### 第11章（s19）で確認したこと

- 条件：Case 07 の質問、gpt-4o-mini、各6回。どちらも、同じ Schema・同じ Guardrail（数値の検査）・作り直し最大3回。違いは責務を分けたかだけ。
- 結果：

| | 成功 | 1回で合格 | データ品質 Tool を実行 | 平均 Token | 平均時間 |
|---|---:|---:|---:|---:|---:|
| 単一 Agent（Tool 6つ） | 6/6 | 6/6 | 6/6 | 5,475 | 9.2 秒 |
| 3 Role（修正前） | 4/6 | **0/6** | 6/6 | 16,591（3.0倍） | 45.5 秒（4.9倍） |
| 3 Role（修正後） | 6/6 | 6/6 | 6/6 | 6,557（1.2倍） | 17.8 秒（1.9倍） |

- **単一 Agent は、データ品質の Tool を飛ばさなかった**（6/6 で実行）。「1人だと確認を飛ばす」は、この条件では成り立たなかった。
- **修正前の3 Role は、業務示唆担当が毎回、構成比を足した文を書いた**（「IT機器とオフィス家具で81.2%」「東北と関西で59.4%」）。Guardrail が却下し、Crew 全体を作り直すため、Token が3倍になった。s08 で見た「示唆担当が比率を作る」と同じ現象。
- 修正：業務示唆担当は、文章（要約・示唆・提言）に数値を書かない。数値は metrics にだけ、前工程の値をそのまま引用する。これで1回で合格するようになった。
- Role の境界違反（Tool を持たない Role が Tool を実行した）は0件。Tool を実行した Role は、Trace の Tool イベントの `role` で判定（委譲の記録では判定しない。s09）。
- 整合性の確認（欠損・重複・外部キー・キャンセルの売上）は、データベースの制約が守っている。データ品質担当が確認するのは、制約で守れないもの（期間・取り込み時刻・キャンセルと要レビューの割合）と、制約が実際に守られていること（0件のはず）。
- Task 生成後に `guardrail` を代入しても働かない（s13）罠は、`build_tasks` に引数で渡して回避。

### 第12章（s20）で確認したこと

- 条件：Case 07 の質問、gpt-4o-mini（Manager も同じモデル）、各6回。同じ3 Role・同じ Task・同じ Guardrail・作り直し最大3回。違いは Process と委譲だけ。

| | 成功 | 1回で合格 | Role の実行回数 | 委譲の回数（合計） | 境界違反 | 平均 Token | 平均時間 |
|---|---:|---:|---:|---:|---:|---:|---:|
| sequential | 6/6 | 6/6 | 3（毎回） | 0 | 0 | 6,489 | 16.7 秒 |
| sequential + `allow_delegation=True` | 6/6 | 6/6 | 3（毎回） | 0 | 0 | 7,249（1.1倍） | 18.3 秒（1.1倍） |
| hierarchical（`manager_llm`） | 6/6 | 4/6 | 4,12,4,4,4,11（平均6.5） | 37 | 0 | 33,362（5.1倍） | 76.8 秒（4.6倍） |

- hierarchical の Role の初回の実行順序は、6回とも 品質→分析→示唆。ただし Manager が順序を守る仕組みはない（`context` と Task の説明による結果）。
- 1回で合格できなかった2回の理由は、記録していない（要追加調査）。
- **罠**: Task は、作る時点で Agent の Tool を `task.tools` に写し取る。`build_tasks()` のあとに `task.agent = None` としても Tool が残り、hierarchical の Manager が、売上の Tool と `check_data_quality` を直接実行した（n=1 の診断で確認。Trace の Tool イベントの `role` が `Crew Manager`）。担当を決めない Task は、作るときに `agent` を渡さない（`assign=False`）。この修正後は境界違反0件。
- 委譲の Tool（`delegate_work_to_coworker`・`ask_question_to_coworker`）はデータを読む権限ではないので、境界の検査から除いた。Manager が売上の Tool を実行した場合は違反。
- 限界: 標本6回・1つの質問・Manager の指示（`manager_agent`）の調整は未検証。

### 第12章の作業中に見つかったこと（s21）

- 地域別の質問（`地域別の売上を分析してください。`、好みなし）が、確率的に Guardrail を通らない。LLM が「東北と関西で約60%（59.4%）」と2地域の構成比を足した文を書き、理由を渡して作り直しても繰り返し、3回を使い切る。会話の続き（「では一番高かった地域は？」）は 8/8 で通る。
- 同じ質問を12〜15回ずつ測った成功数: 現在のコード 8/15・5/12、ch10 のタグ 12/12 と、のちの再測定で 3/12。コードの退行ではなく、時間帯などで変わる確率的な現象（原因は未特定）。第10章の「1回で合格 17/20」は、出にくい時間帯の測定だった可能性が高い。
- 「出力 Schema に選択肢（`integrated_analysis`）を足したことが原因」という仮説は、分離しても改善せず（6/12）、誤りだった。
- 対策: 地域・カテゴリ・商品の Tool が、順位ごとの累積売上と累積構成比（「上位からの累積 16,813,590円・59.4%」）を返し、Guardrail は累積の値も正しい値として認める。LLM が足したくなる値を、Tool 側が出す（BR-03）。累積は、丸めた構成比を足さず、金額を足してから割る。
  - 累積構成比だけ（金額なし）では、2地域の**金額**を足した値（16,813,590円）と「約30%」という丸めが新たに却下された（12/12 成功・1回で合格 8/12）。累積金額も加えた。
  - 結果（地域別・カテゴリ別・商品別の各10回、同じ時間帯に修正前と並行実行）:

| 質問 | 修正前 成功 / 1回で合格 | 修正後 成功 / 1回で合格 |
|---|---:|---:|
| 地域別 | 4 / 1 | 10 / 10 |
| カテゴリ別 | 2 / 0 | 10 / 10 |
| 商品別 | 9 / 5 | 10 / 10 |

  - Case 07（sequential、6回）: 6/6 成功・6/6 が1回で合格。平均 7,037 Token（修正前 6,489。Tool の出力が長くなった分）、16.8 秒。
  - 限界: 標本は各10回、1つのモデル。「累積」以外の足し算（例: 下位2つの合計、2地域の差）は、まだ Tool が返していない。

### 第13章（s22）で確認したこと

- 条件：gpt-4o-mini。A は筆者が作った24問のラベル付き質問（通常10・統合8・レビュー依頼6）。B は「地域別の売上を分析してください。」を8回ずつ。
- A. 経路の選択:

| | 正解 | 通常（10） | 統合（8） | レビュー依頼（6） |
|---|---:|---:|---:|---:|
| 決定論の規則（`choose_route`） | 17/24 | 9/10 | 4/8 | 4/6 |
| LLM（各3回） | 63/72 | 7/10 | 8/8 | 6/6 |

  - 規則の誤りは、「品質」を使わない統合分析の言い換え4問と、レビュー依頼の言い換え2問の取りこぼし（通常の分析へ流れる）。逆向きの誤りは「O0001を登録した場合のレビュー件数は？」の1問（レビュー依頼と判定）。
  - LLM の誤りは、読み取りの質問を状態を変える依頼と判定した2問（3回とも）と、統合と判定した1問。3回とも同じ答え（答えが変わった質問は 0/24）。
  - 限界：質問は筆者が作った。LLM の指示は1種類。折衷案（通常と統合だけ LLM、レビュー依頼は規則）は未測定。規則を測った24問に合わせて直すことは、測定の意味を失わせるので行わない。
- B. Flow のオーバーヘッド: 直接 8/8 成功・8/8 が1回で合格・3,704 Token・8.2 秒、Flow 経由 8/8・8/8・3,705 Token・7.7 秒。Flow は LLM を呼ばないので Token は増えない。
- Flow の経路は Trace（`route_chosen`、各ステップ）に残る。範囲外とレビュー依頼は LLM の呼び出し 0 回（統合テスト）。
- 承認 Flow（`ReviewFlow`）を、分析 Flow のステップの中から呼べる（Flow の入れ子）。一時停止も Trace に出る（`request_approval: paused`）。
- 注意: `from shared.review_flow import start_review` で名前を取り込むため、テストで差し替えるときは `shared.analysis_flow.start_review` を差し替える（`shared.review_flow` を差し替えても効かない）。

### 第14章（s23）で確認したこと

- A. 非同期（1.5秒のブロッキング処理の実行中に `/health` が返るまでの遅れ。測り始めは `sleep` の前）: `async def` で直接 1.31 秒、`asyncio.to_thread` 0.00 秒、同期の `def` 0.00 秒。最初の測定は、`sleep(0.2)` のあとから測ったため、ブロッキングでも 0.00 秒に見えた（ブロッキングが `sleep` 自体を延ばすため）。
- CrewAI の Flow を、イベントループのスレッドで直接呼んでも、例外にはならなかった（承認の経路で確認。LLM を呼ぶ経路では未確認）。「`asyncio.run` で失敗する」という当初の想定は誤りで、問題は、実行中にサービス全体が止まること。
- B. 同時実行（実際の LLM、異なる4問）: 逐次 26.4 秒 → 同時 10.9 秒（各リクエスト 5.8〜10.9 秒）、成功 4/4、`run_id` 重複なし、Trace は実行ごとに分かれ、期待の Tool だけが記録された。好み（実際の Memory）の取得が同時に走っても壊れなかった。1回の測定。
- FastAPI の既定は、422 の応答に入力の値（`input`）を含める。秘密情報を含む質問が、そのまま返る。`RequestValidationError` のハンドラで、項目名と理由だけにした。
- タイムアウトでスレッドは止まらない。時間切れの応答（504）のあとも分析は動き続けるため、枠は、スレッドが終わるまで空けない（空けると、同時実行の上限を超える）。
- 依存関係に `fastapi==0.142.2` を追加（MIT）。

### 第15章（s24 と実機）で確認したこと

- 条件：手元の Docker（macOS、PostgreSQL 17、psycopg 3.3.6、psycopg-pool 3.3.3）。
- A. 接続: 毎回接続を作る 6.99 ms（中央値、最大 21.0 ms）、プール 0.84 ms（最大 2.0 ms）。200回。
- B. 同時の決定（承認と却下を同時に）: 決定の権利なし（PostgreSQL）10/10 で両方が実行（決定 2 件・結果を受け取った人 2 人）。権利あり 10/10 で 1 件・1 人。SQLite では、27 回で 2 件にならなかった（書き込みのロックによる偶然の直列化）。CrewAI の再開は、状態を読んでから、承認待ちの印を消すため、2 人が消される前に読めば、両方が再開できる。
- C. プール: 接続の上限 4、32 スレッド、400 回で 0.18 秒、失敗 0。
- 売上の PostgreSQL 版は、SQLite 版と、全体の集計・データ品質・4つの軸・プレビュー・1件・全件で一致した。PostgreSQL は、真偽値の合計（`SUM(bool)`）ができず、`COUNT(*) FILTER (WHERE ...)` を使う。
- 取り込みは DDL ごと 1 つのトランザクション。途中で失敗すると、元のテーブルとデータが残る。取り込みの間、別スレッドの参照は、常に 300 件（空や途中は見えない）。
- コンテナ: イメージ 1.63 GB（依存関係の層が 1.09 GB）。起動から API が受付可能まで 15 秒。メモリ（待機）api 188 MiB / ui 54 MiB / db 21 MiB。ui のコンテナに秘密情報なし。イメージの履歴にもなし。
- コンテナをすべて作り直しても（`down` → `up`）、承認待ちが PostgreSQL に残り、別のコンテナから承認できた。取り込み（migrate）は、初期化済みなら何もしない。
- 罠: ① CrewAI は import 時に `~/.local/share/...` を作る（ホームが書けないと PermissionError）。② プールの接続に `row_factory` を設定すると、次の借り手が壊れる。③ SQLite 版が、制約違反をすべて重複と扱い、監査ログの失敗が重複と報告された。
- 限界: 手元の Docker 1 台。複数の API コンテナの同時実行は未確認。Memory（LanceDB）と Trace はコンテナごとのファイル。バックアップ・マイグレーション・暗号化通信・読み取り専用ロールは未対応。

### 第16章で確認したこと

- **CrewAI AMP（Managed Route）**: 実際の配置はしていない（アカウント・課金が必要）。公式ドキュメント（2026-10-05）と、手元の CLI で確認。配置は CLI（`crewai deploy create / push / status / logs / list / remove / validate`）、GitHub 連携の Web 画面、API による再配置。呼び出しは REST（`/inputs`・`/kickoff`・`/status/{id}`）で、Bearer トークン。環境変数はダッシュボードで設定。要件は `src/<パッケージ>/` のレイアウト、`[tool.crewai] type = "crew" | "flow"`、コミットされた `uv.lock`。読んだページには、ロールバック・スケーリング・料金の記載がない（別のページは未確認）。
- `crewai deploy validate`（プラットフォームに接続しない）をこのリポジトリで実行すると `Missing src/ directory`（src レイアウトが必要）。`crewai create flow` の雛形は、ロックファイルを作ると通る。
- 実機（macOS の Docker Desktop）: `docker-compose.prod.yml`。認証なし・誤ったトークンは 401、他人の名前は 403、API の仕様は 404。コンテナの設定に秘密情報の値がない。`/app` は読み取り専用、`CapEff` は空、`NoNewPrivs` は 1。安全でない設定（秘密情報なし）で本番として起動すると、`auth_missing` などで起動に失敗（値は出ない）。プロセスが終了すると約 8 秒で再起動、`docker kill`（手動の停止）では再起動しない。
- 不具合: ① 秘密情報をファイルで渡すと起動に失敗（ファイルの値を環境へ出したあとの検査が、両方の設定と判定）。② 取り込みのコンテナが `DATABASE_URL_FILE` を読めなかった。③ アクセスの記録が本番で出力されなかった（ログの設定がなく、`caplog` のテストは通っていた）。④ 承認者の本人確認の検査を外しても全テストが通った（役割の検査が隠していた）。
- 限界: クラウドへの配置なし。Linux のホストでの、秘密情報のファイルの権限は未確認。ブラウザで、実際のトークンを入力する確認はしていない（ログイン画面と、誤った入力の拒否のみ）。TLS、ログイン試行の制限、SSO は未対応。

## 未解決・注意

- モデルは `gpt-4o-mini` で検証した。本書で使うモデルは、出版時点で再確認して決める。
- 1回の実行結果である。LLM の出力は確率的なため、Case の合格率は複数回実行で測る必要がある（`evaluation_spec.md` §4）。
- Windows など別環境での動作は未確認。
