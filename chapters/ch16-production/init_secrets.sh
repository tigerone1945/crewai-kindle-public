#!/bin/sh
# 本番に近い構成（docker-compose.prod.yml）の秘密情報を、ファイルとして用意する（第16章）。
# 使い方: OPENAI_API_KEY=<キー> sh chapters/ch16-production/init_secrets.sh [出力先（既定 ./secrets）]
# - DB のパスワードは、ランダムに作る（人が決めない）。database_url は、そのパスワードから組み立てる。
# - 最初の資格情報（分析者と承認者）を作る。トークンは、ここで1度だけ表示する。保存されるのは、ダイジェストだけ。
# - 既存のファイルは、上書きしない。出力先は、所有者だけが読める（700 / 600）。値は、コミットしない（secrets/ は .gitignore）。
set -eu
umask 077
dir="${1:-./secrets}"
[ -n "${OPENAI_API_KEY:-}" ] || { echo "OPENAI_API_KEY を環境変数で渡してください（ファイルへ書き込みます）" >&2; exit 2; }
mkdir -p "$dir"
for f in postgres_password database_url openai_api_key auth_credentials.json; do
  [ ! -e "$dir/$f" ] || { echo "$dir/$f は、すでにあります。上書きしません" >&2; exit 2; }
done
password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf '%s' "$password" > "$dir/postgres_password"
printf 'postgresql://app:%s@db:5432/sales' "$password" > "$dir/database_url"
printf '%s' "$OPENAI_API_KEY" > "$dir/openai_api_key"
creds="$dir/auth_credentials.json"
echo "--- 分析者（alice）のトークン（1度だけ表示）:" >&2
uv run python chapters/ch16-production/manage_credentials.py --file "$creds" create --id alice --actor user-001 --roles analyst --days 90
echo "--- 承認者（approver-1）のトークン（1度だけ表示）:" >&2
uv run python chapters/ch16-production/manage_credentials.py --file "$creds" create --id approver-1 --actor approver-001 --roles approver --days 90
chmod 600 "$dir"/*
echo "用意しました: $dir（コミットしないでください）" >&2
