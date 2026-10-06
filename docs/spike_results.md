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
| — | Production（AMP 等）/ MCP | 16, 付録K | 未実施 |

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

## 未解決・注意

- モデルは `gpt-4o-mini` で検証した。本書で使うモデルは、出版時点で再確認して決める。
- 1回の実行結果である。LLM の出力は確率的なため、Case の合格率は複数回実行で測る必要がある（`evaluation_spec.md` §4）。
- Windows など別環境での動作は未確認。
