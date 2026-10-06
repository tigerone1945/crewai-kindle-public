# 第15章 DockerとPostgreSQLで実行環境を整える

UI・API・データベースを、別々のコンテナとして、1つのコマンドで起動します。売上・承認の記録・Flow の状態を PostgreSQL に置き、アプリのコンテナを、状態を持たない（Stateless）ものにします。

```text
docker compose up
  ui（キーも DB の接続先も持たない） ─ HTTP ─ api（非 root。キーと接続先を持つ） ─ db（PostgreSQL。ポートは公開しない）
                                           migrate（初回だけ。初期化済みなら何もしない）
```

## 起動

```bash
export POSTGRES_PASSWORD=<パスワード>     # または .env（コミットしない）
export OPENAI_API_KEY=<キー>              # または .env
docker compose up --build                 # ui: http://127.0.0.1:8501 / api: http://127.0.0.1:8000/docs
docker compose down                       # コンテナを消す（データは、ボリュームに残る）
docker compose down -v                    # データも消す
```

初回の取り込みは `migrate` が行います。初期化済みなら何もしません（運用中のデータを消さない）。作り直すには `docker compose run --rm migrate python chapters/ch15-docker-postgresql/import_to_postgres.py --force`。

## 決めたこと・測ったこと

| | 内容 |
|---|---|
| イメージ | 2段階のビルド。`uv.lock` のとおりに入れる。非 root。秘密情報を入れない。1.63 GB（依存関係が 1.09 GB） |
| 起動 | api が受付可能になるまで 15 秒。メモリ（待機）: api 188 MiB、ui 54 MiB、db 21 MiB |
| 秘密情報 | ui のコンテナの環境変数に、DB の接続先も LLM のキーもない。イメージの履歴にもない |
| 接続プール | 1問い合わせ 6.99 ms（毎回接続）→ 0.84 ms（プール）。上限 4・32 スレッド・400 回で失敗 0 |
| 同時の決定 | 決定の権利なし: 10/10 で承認と却下の両方が実行された。権利あり: 10/10 で 1 件 |
| Stateless | コンテナをすべて作り直しても、承認待ちが残り、別のコンテナから承認できた |

## 罠

- CrewAI は `import` 時に `~/.local/share/...` へフォルダーを作る。ホームが書けないユーザーで、起動できない
- プールした接続に、`row_factory` などの状態を設定しない（次の借り手が壊れる）。カーソルに指定する
- 同時の承認と却下は、SQLite では偶然 1 件になるが、PostgreSQL では両方実行された。決定の権利を、DB の主キーで 1 人に絞る
- 制約違反を、すべて「重複」と扱うと、監査ログの失敗が重複と報告される

## 確認

```bash
# PostgreSQL の実機を使うテスト（35件）。未設定ならスキップ
docker run -d --name crewai-kindle-pg -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=sales -p 127.0.0.1:55432:5432 postgres:17
export TEST_DATABASE_URL=postgresql://postgres:devpassword@127.0.0.1:55432/sales
uv run pytest tests/postgres
uv run python examples/spike/s24_postgres.py        # 接続プール・同時の決定・プールの上限の実測
```

## ファイル

| ファイル | 内容 |
|---|---|
| `../../Dockerfile`、`../../.dockerignore`、`../../docker-compose.yml` | イメージと、4つのサービス |
| `import_to_postgres.py` | CSV の取り込み（初期化済みなら何もしない。`--force`） |
| `../../shared/postgres.py` | 接続プール・トランザクション・接続失敗の扱い |
| `../../shared/postgres_sales.py` | 売上（SQLite 版と同じ入口・同じ結果） |
| `../../shared/postgres_review.py` | 承認の記録・監査ログ・決定の権利 |
| `../../shared/postgres_flow_persistence.py` | Flow の状態（承認待ちの再開） |
| `../../examples/spike/s24_postgres.py` | 実測 |

依存関係に `psycopg[binary]` と `psycopg-pool`（どちらも LGPL-3.0）を追加しました。
