# 第17章 ObservabilityでCrew / Flowを運用する

第6章の Trace は、「1回の実行を、あとから辿る」ためのものでした。運用では、その前に「いま、全体として、おかしくないか」を知る必要があります。この章では、実行ごとの記録（Log）、数える仕組み（Metric）、基準を超えたときの知らせ（Alert）、失敗の根本の層の判定を加えます。

| | 内容 |
|---|---|
| Log | 実行が終わるたびに、標準出力へ1行（`run_completed`）。`run_id` で、Trace・アクセスの記録・エラー応答と結びつく。質問・回答・Tool の中身は、持たない |
| Metric | `/metrics`（Prometheus の形式）。実行・失敗の層・所要時間・Token・Tool・HTTP・同時実行数・承認待ち。ラベルは、決まった値だけ |
| 失敗の層 | `input` / `output_validation` / `tool` / `llm` / `agent` / `task` / `crew` / `flow` / `service`。**最初に error になった層**が根本（上の層の error は、伝わった結果） |
| Alert | 直近15分の窓で、9つの規則（Runtime Health / Agent Quality / Business Quality）。件数が少ないうちは判定しない。始まりと終わりだけ、1行出す |
| 権限 | `operator` の役割だけが、`/metrics` と `/v1/ops/status` を見られる。分析も承認もできない |
| Trace の保存期間 | 14日・5,000件を超えたものを消す（1時間に1回）。`TRACE_RETENTION_DAYS` / `TRACE_MAX_FILES` |

## 使い方

```bash
# 第16章の構成を起動（運用者の資格情報 ops-1 も、init_secrets.sh が作る）
docker compose -f docker-compose.prod.yml up -d --build

curl -H "Authorization: Bearer <operator のトークン>" localhost:8000/metrics
curl -H "Authorization: Bearer <operator のトークン>" localhost:8000/v1/ops/status

# ログから集計（先週の火曜に何が起きたか。Metric は再起動で消えるが、ログは残る）
docker compose -f docker-compose.prod.yml logs --no-log-prefix api | uv run python chapters/ch17-observability/ops_report.py --since-minutes 60

# 1件の実行の中を見る（run_id は、応答の X-Run-Id・エラー応答・ログにある）
docker compose -f docker-compose.prod.yml exec api python chapters/ch17-observability/inspect_run.py <run_id> --dir /app/data/traces
```

## 実機で確かめたこと

| | 結果 |
|---|---|
| 権限 | `/metrics`: トークンなし 401、分析者 403、運用者 200 |
| 実行の記録 | 分析2件・却下1件 → `analyses_total`・`analysis_failures_total{layer="input"}`・Token・Tool・HTTP が、期待どおり。ログに質問・トークンなし |
| 集計・調査 | コンテナのログを `ops_report.py` へ、コンテナの中で `inspect_run.py`（Trace は `apidata` に保存） |
| 故障の注入（本物の Crew・LLM、各2回） | 根本の層の判定が期待と 12/12 で一致（下の表） |

| 故障 | 判定の層 | HTTP | 試行 | LLM 呼び出し | Tool エラー |
|---|---|---|---|---|---|
| なし | （なし） | 200 | 1 | 2 | 0 |
| 範囲外の質問 | input | 422 | 0 | 0 | 0 |
| Tool が例外 | tool | 503 | 3 | 11 | 7 |
| LLM のキーが無効 | llm | 503 | 3 | 18 | 0 |
| 出力の検査が必ず却下 | output_validation | 502 | 1 | 6 | 0 |

## 見つかった不具合・設計の穴

- **遅さの原因が、Trace に出ていなかった**。Tool に 4 秒の遅れを入れると、全体は 42 秒かかった。Trace の Tool は 4 秒で、残りの約 38 秒は、出力の検査（Guardrail）が、検査のたびに同じ集計を取り直していた時間。検査の時間を Trace に残し（`check`）、集計を1回の実行の間は使い回す（`SnapshotRepository`）ようにした。同じ条件で 27 秒（Tool 4.0 + 検査 16.0 + LLM 6.8）。
- Trace の保存と運用の記録を、Event の取り込み（flush）より前に行っていた（保存の位置が、`trace_run` の中だった）。取り込みのあとへ移した。
- Trace が、書き続けるだけで、消えなかった（保存期間の追加）。

## 限界

- Metric は、プロセスのメモリ。再起動で0に戻り、ワーカーを増やすと、ワーカーごとの値になる（`prometheus_client` の multiprocess モードが必要）。複数コンテナでは、収集の側（Prometheus など）で足し合わせる。
- Alert の窓も、プロセスの中。通知の送り先（メール・チャット）は、実装していない（標準出力の `alert_firing` を、ログの収集から送る）。Prometheus・Grafana は、実際には動かしていない。
- 基準の値は、例。自分のサービスで、平常時を測ってから決める。
- Trace は、コンテナごとのファイルのまま（外部の Trace 基盤・OpenTelemetry は、扱っていない）。
- 「正しい回答だったか」は、観測できない。出力の検査を通ったことしか、分からない（第18章）。

## 確認

```bash
uv run pytest tests/unit                                       # 失敗の層・Metric・Alert・API・道具（LLM を呼ばない）
uv run pytest tests/integration/test_ch17_observability.py     # 本物の Crew・LLM で、層の判定を確かめる（APIキーが必要）
uv run python examples/spike/s25_failure_injection.py 2        # 故障の注入（本文の実測）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `shared/observability.py` | RunRecord・失敗の層の判定・Observer（Log・Metric・窓） |
| `shared/alerts.py` | 指標・規則・Alert・AlertTracker |
| `shared/api.py` | `/metrics`・`/v1/ops/status`・実行の記録・Trace の削除 |
| `shared/tracing.py` | `prune_traces` |
| `ops_report.py` / `inspect_run.py` | ログの集計 / 1件の Trace の表示 |
