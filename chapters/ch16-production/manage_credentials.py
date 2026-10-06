"""資格情報（アクセストークン）の発行・一覧・失効（第16章）。

- 発行: トークンを、1度だけ、標準出力へ表示する。ファイルに保存するのは、SHA-256 のダイジェストだけ（トークンは、あとから取り出せない）。
- 失効: 資格情報の行を消す。サービスは、ファイルを起動時に読むので、反映には、サービスの再起動が要る（再起動なしの失効は、この章では扱わない）。
- ファイルは、所有者だけが読み書きできる（600）。
使い方:
  uv run python chapters/ch16-production/manage_credentials.py --file secrets/auth_credentials.json create --id alice --actor user-001 --roles analyst --days 90
  uv run python chapters/ch16-production/manage_credentials.py --file secrets/auth_credentials.json list
  uv run python chapters/ch16-production/manage_credentials.py --file secrets/auth_credentials.json revoke --id alice
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.auth import credential_to_dict, load_credentials, new_credential  # noqa: E402


def read(path: Path) -> list:
    return load_credentials(path.read_text(encoding="utf-8")) if path.exists() and path.read_text(encoding="utf-8").strip() else []


def write(path: Path, credentials: list) -> None:
    """所有者だけが読める（600）状態で、置き換える。途中で失敗しても、元のファイルは壊れない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".creds-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([credential_to_dict(c) for c in credentials], f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def create(path: Path, credential_id: str, actor_id: str, roles: list[str], days: int | None) -> str:
    credentials = read(path)
    if any(c.id == credential_id for c in credentials):
        raise SystemExit(f"id {credential_id} は、すでにあります。先に失効（revoke）してください")
    expires = datetime.now(timezone.utc) + timedelta(days=days) if days else None
    token, credential = new_credential(credential_id, actor_id, roles, expires)
    write(path, [*credentials, credential])
    return token


def revoke(path: Path, credential_id: str) -> bool:
    credentials = read(path)
    remaining = [c for c in credentials if c.id != credential_id]
    if len(remaining) == len(credentials):
        return False
    write(path, remaining)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create")
    c.add_argument("--id", required=True)
    c.add_argument("--actor", required=True)
    c.add_argument("--roles", required=True, help="analyst,approver のカンマ区切り")
    c.add_argument("--days", type=int, help="有効日数（省略すると無期限。本番では、期限を付ける）")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        token = create(args.file, args.id, args.actor, [x.strip() for x in args.roles.split(",") if x.strip()], args.days)
        print(token)  # 1度だけ。この値は、再表示できない
        print("↑ このトークンは、いま表示した1度だけです。安全な方法で、利用者へ渡してください。", file=sys.stderr)
    elif args.command == "list":
        for cred in read(args.file):
            print(f"{cred.id}\t{cred.actor_id}\t{','.join(sorted(cred.roles))}\t{cred.expires_at.isoformat() if cred.expires_at else '無期限'}")
    elif args.command == "revoke":
        if not revoke(args.file, args.id):
            print(f"id {args.id} は、ありません", file=sys.stderr)
            return 1
        print("失効しました（サービスの再起動で反映されます）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
