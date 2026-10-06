# 第16章 CrewAIをProductionへDeployする

本番に出すために決めること（Managed vs Container の比較、秘密情報、認証・認可、監査、設定の検査）を、コードと設定で実装し、1台のホストの本番に近い構成で確かめます。**クラウドへは配置していません。CrewAI AMP にも、配置していません**（アカウントと課金が必要なため）。

| | 内容 |
|---|---|
| 認証 | アクセストークン（256 ビットの乱数）。サービスが持つのは SHA-256 のダイジェストだけ。定数時間で照合。期限・失効 |
| 認可 | 役割（analyst / approver）+ 「名乗る利用者 = 認証された本人」。他人の名前での分析・承認は 403。本人は、自分の依頼を承認できない |
| 秘密情報 | `NAME` または `NAME_FILE`（Docker secrets）。`docker inspect` に値が出ない。ログ・エラー・検査の結果に値を出さない |
| 設定の検査 | `APP_ENV=production` で、起動の前に検査。error があれば起動しない（認証・DB・パスワード・LLM キー）。warn は、DB の暗号化・テレメトリなど |
| アクセスの記録 | 標準出力へ、1行1件の JSON。トークン・質問は残さない |
| コンテナの硬化 | 読み取り専用のルート、`cap_drop: ALL`、非 root、リソースの上限、異常終了で再起動 |

## 使い方

```bash
# 1. 秘密情報を用意する（secrets/ に作る。コミットしない）。トークンは、表示された1度だけ
OPENAI_API_KEY=<キー> sh chapters/ch16-production/init_secrets.sh

# 2. 本番に近い構成で起動する（APP_ENV=production。設定が足りなければ起動しない）
docker compose -f docker-compose.prod.yml up -d --build      # api: 127.0.0.1:8000 / ui: 127.0.0.1:8501（ログイン画面）

# 設定を検査だけする（値は表示しない）
python -m shared.config

# 資格情報の発行・一覧・失効（失効の反映は、サービスの再起動）
uv run python chapters/ch16-production/manage_credentials.py --file secrets/auth_credentials.json create --id carol --actor user-002 --roles analyst --days 90
```

**TLS はありません。** 外へ公開するときは、TLS を終端する入口が必須です（トークンが平文で流れるため）。

## 実機で確かめたこと

| | 結果 |
|---|---|
| 認証 | トークンなし・誤り: 401。他人の名前: 403。承認の一連: 依頼者の承認は 403、承認者の承認で登録。`/docs` と `/openapi.json` は 404 |
| 秘密情報 | コンテナの設定に値がない（`_FILE` の場所だけ）。ログにトークン・質問が出ない |
| 起動の検査 | 秘密情報なしで本番として起動 → `auth_missing`・`database_missing`・`llm_key_missing` で起動に失敗（値は出ない） |
| 硬化 | `/app` への書き込みは失敗（読み取り専用）。`CapEff` は空、`NoNewPrivs` は 1 |
| 再起動 | プロセスの終了 → 約 8 秒で再起動して受付可能。`docker kill`（手動の停止）→ 再起動しない |

## 見つかった不具合

- 秘密情報をファイルで渡すと、必ず起動に失敗した（起動のときに、ファイルの値を環境変数へ出し、そのあとの検査が「両方が設定されている」と判定した）。単体テストで発見
- 取り込みのコンテナが `DATABASE_URL_FILE` を読めなかった。実機で発見
- **アクセスの記録が、本番で 1 行も出なかった**。Python のログは、設定しないと INFO を出さない。`caplog` を使うテストは通っていた。標準出力へ出す設定と、出力そのもののテストを足した。実機で発見
- 承認者の名前の一致の検査を外しても、全テストが通った（役割の検査が先に働いて隠していた）。別の承認者を使うテストを足した

## 確認

```bash
uv run pytest tests/unit/test_auth.py tests/unit/test_config.py tests/unit/test_api_auth.py tests/unit/test_ch16_ui.py tests/unit/test_manage_credentials.py
```

## ファイル

| ファイル | 内容 |
|---|---|
| `../../shared/auth.py` | 資格情報・認証・認可 |
| `../../shared/config.py` | 秘密情報の読み取り・本番の検査 |
| `../../shared/api.py` | API への認証・認可・アクセスの記録・起動の検査の組み込み |
| `../../shared/api_client.py` | トークンを送るクライアント |
| `ui.py` | ログインつきの画面 |
| `manage_credentials.py`、`init_secrets.sh` | 資格情報・秘密情報の用意 |
| `../../docker-compose.prod.yml` | 本番に近い構成（Docker secrets、硬化、再起動） |
