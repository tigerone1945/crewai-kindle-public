# 第12章 CrewとProcessでMulti-Agentを動かす

第11章の3つの Role を、誰が・どの順に・どう動かすか（Crew と Process）を扱います。sequential（順序と担当をコードで決める）と hierarchical（Manager が決める）を、同じ条件で測って比べます。

| | 順序・担当を決めるのは | 測った結果（Case 07、各6回、gpt-4o-mini） |
|---|---|---|
| sequential（既定） | Task の定義（コード） | 6/6 成功、6/6 が1回で合格、6,489 Token、16.7 秒 |
| sequential + 委譲を許す | Task の定義 | 委譲は0回。Token 1.1倍 |
| hierarchical | Manager（LLM） | 6/6 成功、4/6 が1回で合格、33,362 Token（5.1倍）、76.8 秒（4.6倍）。Role の実行回数は 4〜12 回 |

この本の判断: やることと順序が事前に分かる仕事は、sequential にします。安全に関わるもの（権限・Guardrail・作り直し）は、Process が何であっても、コードに残します。

## 使い方

```python
import shared.analysis_service as service

service.run_multi_role_analysis("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。")                         # sequential
service.run_multi_role_analysis("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。", process="hierarchical")  # 実験的
```

## 罠: Task は、作るときに Tool を写し取る

担当を決めない Task を、作ったあとに `task.agent = None` で作ると、Agent の Tool が Task に残り、Manager が Tool を直接実行します（権限の境界が静かに破れる）。担当を決めない Task は、`build_tasks(..., assign=False)` で、作るときから担当を渡しません。

## 確認

```bash
uv run pytest tests/unit/test_crew_process.py                     # Crew の構成・権限境界（LLMを呼ばない）
uv run pytest tests/integration/test_ch12_crew_process.py         # sequential / hierarchical の Trace（APIキーが必要。数分かかる）
uv run python examples/spike/s20_process_comparison.py 6          # 3構成の比較（APIキーが必要。約10分。引数: 回数、sequential,delegation,hierarchical）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `../../shared/analysis_service.py` | `build_role_crew(process=...)`、`run_multi_role_analysis(process=...)` |
| `../../shared/roles.py` | `build_tasks(assign=...)`、`DELEGATION_TOOLS`、`delegation_count`、境界の検査 |
| `../../examples/spike/s20_process_comparison.py` | 3構成の比較 |
