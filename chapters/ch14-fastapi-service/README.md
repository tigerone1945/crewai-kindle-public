# 第14章 FastAPIでCrewAIをService化する

分析（第13章の Flow）を、HTTP の Service（FastAPI）として切り出し、画面（Streamlit）を、その API を呼ぶだけにします。

```text
ブラウザ ─ Streamlit（ui.py） ─ HTTP（契約: /v1） ─ FastAPI（shared/api.py） ─ Flow ─ Crew ─ LLM
                                                    └ Memory、SQLite、Trace
```

| 決めたこと | 内容 |
|---|---|
| 契約 | `shared/api_contract.py`。CrewAI に依存しない。入力は厳密（未知の項目は拒否・長さの上限）、出力は見せてよい項目だけ |
| エラーモデル | 全て同じ形（`{"error": {"code", "message", "run_id", ...}}`）。422 / 403 / 409 / 429 / 502 / 503 / 504 / 500 |
| 非同期 | 分析は `asyncio.to_thread`。`async def` の中で直接ブロッキングすると、他の全リクエストが待たされる（1.5 秒の処理で `/health` が 1.31 秒遅れた） |
| 同時実行 | 上限 4。超えたら 429。時間切れ（150 秒）は 504 だが、スレッドは止められないので、終わるまで枠を空けない |
| 反射しない | FastAPI の既定は、422 の応答に入力の値を含める。秘密情報を返さないよう、エラーの形を自前で定める |
| 範囲外 | 認証・認可は第16章。`actor_id` は本人確認ではない。既定で `127.0.0.1` だけで待ち受ける |

## 起動

```bash
uv run uvicorn --factory shared.api:create_app --host 127.0.0.1 --port 8000   # サービス
uv run streamlit run chapters/ch14-fastapi-service/ui.py                     # 画面（別のプロセス）
```

接続先は、環境変数 `ANALYSIS_API_URL`（既定 `http://127.0.0.1:8000`）。API の仕様は、起動後に http://127.0.0.1:8000/docs。

依存関係に `fastapi`（MIT ライセンス）を追加しました。`uvicorn` と `httpx` は、CrewAI の依存として入っています。

## 測ったこと

| | 結果 |
|---|---|
| ブロッキングの影響（1.5 秒の処理） | `async def` で直接: `/health` が 1.31 秒遅れる。`to_thread` / `def`: 0.00 秒 |
| 同時に 4 件（実際の LLM） | 逐次 26.4 秒 → 同時 10.9 秒（成功 4/4、Trace は混ざらない） |

## 確認

```bash
uv run pytest tests/unit/test_api.py tests/unit/test_api_client.py tests/unit/test_ch14_ui.py   # 契約・エラー・同時実行・画面（LLMを呼ばない）
uv run pytest tests/integration/test_ch14_api.py                                                # 本物の Flow・Crew・LLM（APIキーが必要）
uv run python examples/spike/s23_async_service.py AB                                            # 非同期の実測（A）と同時実行の実測（B。APIキーが必要）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `../../shared/api_contract.py` | Request / Response / Error の型 |
| `../../shared/api.py` | FastAPI のサービス（`create_app`、`Dependencies`） |
| `../../shared/api_client.py` | 利用側のクライアント（`AnalysisApiClient`、`ApiError`） |
| `../../shared/result_view.py` | 結果の表示（CrewAI に依存しない。`ui_components` から分離） |
| `ui.py` | 画面（API だけを呼ぶ） |
| `../../examples/spike/s23_async_service.py` | 非同期と同時実行の測定 |
