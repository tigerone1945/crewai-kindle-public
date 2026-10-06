# 第3章 Structured Outputで分析結果を安定させる

Agent の出力を、文章ではなく Pydantic の Schema（`SalesAnalysisResult`）で受け取ります。
Schema に適合するかどうかに加えて、数値が実データと一致するかも検証します。

## 実行

リポジトリのルートで実行します。

```bash
uv run python chapters/ch03-structured-output/main.py "地域別の売上を分析してください。"
uv run python chapters/ch03-structured-output/main.py "来月の売上を予測してください。"
```

結果は JSON で表示されます。対応する Tool がない質問は `analysis_type: "unsupported"` になります。

## 確認

```bash
uv run pytest tests/unit        # Schema・数値検証（LLMを呼ばない。毎回同じ結果）
uv run pytest tests/integration # LLMを呼ぶ。出力は確率的なため、まれに失敗することがある
```

## ファイル

| ファイル | 内容 |
|---|---|
| `main.py` | 実行用のコマンド（質問を受けて結果を JSON で表示） |
| `../../shared/analysis_service.py` | Task の `output_pydantic` と、検証付きの `run_analysis()`（第4章以降の画面からも使う） |
| `../../shared/schemas.py` | `SalesAnalysisResult` / `Metric`（契約） |
| `../../shared/sales_validation.py` | 数値が実データと一致するかの検証 |

## 失敗したとき

`run_analysis()` は、Schema に適合しない、または数値が実データと一致しない場合に、理由を伝えて最大3回まで再試行します（第3章の時点では `run_analysis()` 自身がループで再試行していました。第7章で、検査を CrewAI の Task Guardrail に移しましたが、却下されたときに理由を加えて Crew を作り直す方式は、このときのまま残しています）。
それでも通らない場合は `AnalysisError` を送出します。呼び出し側で捕捉してください（第4章以降の画面・API で扱います）。
