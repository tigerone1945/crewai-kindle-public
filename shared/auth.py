"""認証と認可（第16章）: 誰が呼んだか（Authentication）と、何をしてよいか（Authorization）。

第14章までの `actor_id` は、自己申告だった。誰でも他人の ID を名乗れ、承認者になれた。ここで、ID を、資格情報（トークン）から決める。

- 資格情報は、トークン（高いエントロピーの乱数）。サービスが持つのは、SHA-256 のダイジェストだけ。トークンそのものは、発行時に1度だけ表示し、保存しない。
  トークンは乱数（256 ビット）なので、パスワードのような低速のハッシュは不要（総当たりで当てられない）。人が決めた文字列には、この方式を使わない。
- 照合は、定数時間（hmac.compare_digest）。有効期限・失効（資格情報の削除）に対応する。
- 役割（Role）は、analyst（分析・自分の好み）と approver（承認待ちの閲覧・承認・却下）。承認者は、別の役割として持たせる。operator は、運用の観測（/metrics・/v1/ops/status）だけ。分析も承認もできない。
- 401（認証できない）と 403（認証できたが、許されない）を分ける。失敗の理由は、利用者へ細かく返さない（トークンの有無・期限切れ・不明を、区別して教えない）。
- ここは、CrewAI にも FastAPI にも依存しない。
"""
import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shared.memory_policy import check_actor_id

ROLES = ("analyst", "approver", "operator")
TOKEN_BYTES = 32


class AuthError(Exception):
    """認証・認可の失敗。`code` は unauthenticated（401）か forbidden（403）。`reason` は、ログのための内部の理由（利用者には返さない）。"""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Credential:
    id: str  # 資格情報の識別子（ログに残す。トークンではない）
    token_sha256: str
    actor_id: str
    roles: frozenset[str]
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Identity:
    credential_id: str
    actor_id: str
    roles: frozenset[str]

    def require_role(self, role: str) -> None:
        if role not in self.roles:
            raise AuthError("forbidden", f"missing_role:{role}")

    def require_actor(self, actor_id: str) -> None:
        """リクエストが名乗る利用者が、認証された本人であること。他人の名前で、操作させない。"""
        if actor_id != self.actor_id:
            raise AuthError("forbidden", "actor_mismatch")


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_credential(credential_id: str, actor_id: str, roles: Iterable[str], expires_at: datetime | None = None) -> tuple[str, Credential]:
    """資格情報を発行する。トークンは、この戻り値でしか得られない（保存されるのは、ダイジェストだけ）。"""
    roles = frozenset(roles)
    if not roles or not roles <= set(ROLES):
        raise ValueError(f"roles は {ROLES} のいずれか（1つ以上）にしてください")
    check_actor_id(actor_id)
    token = generate_token()
    return token, Credential(credential_id, hash_token(token), actor_id, roles, expires_at)


def credential_to_dict(c: Credential) -> dict:
    return {"id": c.id, "token_sha256": c.token_sha256, "actor_id": c.actor_id, "roles": sorted(c.roles),
            "expires_at": c.expires_at.isoformat() if c.expires_at else None}


def credential_from_dict(d: dict) -> Credential:
    expires = datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None
    if expires is not None and expires.tzinfo is None:
        raise ValueError(f"資格情報 {d.get('id')} の expires_at に、タイムゾーンがありません")
    roles = frozenset(d["roles"])
    if not roles or not roles <= set(ROLES):
        raise ValueError(f"資格情報 {d.get('id')} の roles が不正です")
    if len(d["token_sha256"]) != 64 or not all(c in "0123456789abcdef" for c in d["token_sha256"]):
        raise ValueError(f"資格情報 {d.get('id')} の token_sha256 が、SHA-256 の16進数ではありません")
    return Credential(d["id"], d["token_sha256"], check_actor_id(d["actor_id"]), roles, expires)


def load_credentials(text: str) -> list[Credential]:
    credentials = [credential_from_dict(d) for d in json.loads(text)]
    ids = [c.id for c in credentials]
    if len(ids) != len(set(ids)):
        raise ValueError("資格情報の id が重複しています")
    return credentials


def load_credentials_file(path: str | Path) -> list[Credential]:
    return load_credentials(Path(path).read_text(encoding="utf-8"))


class Authenticator:
    def __init__(self, credentials: Iterable[Credential], clock=lambda: datetime.now(timezone.utc)) -> None:
        self._credentials = list(credentials)
        self._clock = clock

    def authenticate(self, authorization: str | None) -> Identity:
        """`Authorization: Bearer <token>` から、本人を決める。できなければ AuthError("unauthenticated")。"""
        if not authorization:
            raise AuthError("unauthenticated", "missing_header")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("unauthenticated", "malformed_header")
        digest = hash_token(token.strip())
        match = None
        for credential in self._credentials:  # 一致しても、残りも照合する（照合にかかる時間から、どれに一致したかを推測させない）
            if hmac.compare_digest(digest, credential.token_sha256) and match is None:
                match = credential
        if match is None:
            raise AuthError("unauthenticated", "unknown_token")
        if match.expires_at is not None and match.expires_at <= self._clock():
            raise AuthError("unauthenticated", f"expired:{match.id}")
        return Identity(match.id, match.actor_id, match.roles)

    @property
    def credentials(self) -> list[Credential]:
        return list(self._credentials)
