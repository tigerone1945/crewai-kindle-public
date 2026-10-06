"""設定と秘密情報（第16章）: 秘密情報の受け取り方と、本番へ出してよい設定かの検査（Production Readiness）。

秘密情報（LLM のキー・DB のパスワード・資格情報）:
- 環境変数 NAME、またはファイルを指す NAME_FILE（Docker / Kubernetes の secrets が、ファイルとして渡す）。両方あって値が違えば、曖昧なので、起動しない（同じ値なら、apply_secret_files の結果として許す）。
  環境変数は、`docker inspect` やプロセスの一覧から見えうる。ファイルは、権限で守れる。
- 値は、ログにも、エラー文にも出さない。検査の結果にも、値は含めない（名前と理由だけ）。

Production Readiness:
- APP_ENV=production のとき、起動の前に検査する。error があれば、起動しない（動いてから気づくより、起動で止める）。
- 検査は、「本番に出してはいけない設定」の一覧。すべてを保証するものではない（TLS の証明書の検証、ネットワークの閉じ方などは、検査できない）。
"""
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SECRET_NAMES = ("OPENAI_API_KEY", "DATABASE_URL", "AUTH_CREDENTIALS")  # NAME_FILE の形でも受け取る
WEAK_PASSWORDS = {"password", "devpassword", "devpassword-local", "postgres", "admin", "changeme", "secret", "123456"}
MIN_PASSWORD_LENGTH = 16
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TLS_MODES = {"require", "verify-ca", "verify-full"}


class ConfigError(RuntimeError):
    """設定が、起動してよい状態でない。メッセージは、名前と理由だけ（値を含まない）。"""


@dataclass(frozen=True)
class Finding:
    severity: str  # error / warn
    code: str
    message: str


def read_secret(name: str, env=None) -> str | None:
    """NAME または NAME_FILE から、秘密情報を読む。どちらもなければ None。両方あれば ConfigError。"""
    env = os.environ if env is None else env
    direct, file_path = env.get(name), env.get(f"{name}_FILE")
    if file_path:
        try:
            value = Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            raise ConfigError(f"{name}_FILE が指すファイルを読めません") from None
        if not value:
            raise ConfigError(f"{name}_FILE が指すファイルが空です")
        # 両方が設定され、内容が違えば、どちらが正しいか分からない。同じなら、apply_secret_files の結果（起動のあと）なので、許す
        if direct and direct != value:
            raise ConfigError(f"{name} と {name}_FILE の両方が、異なる値で設定されています。どちらか一方にしてください")
        return value
    return direct or None


def apply_secret_files(env=None) -> list[str]:
    """NAME_FILE があれば、その内容を NAME としてプロセスの環境変数に入れる（LLM のライブラリなどが、NAME を読むため）。入れた名前を返す。"""
    env = os.environ if env is None else env
    applied = []
    for name in SECRET_NAMES:
        if env.get(f"{name}_FILE") and not env.get(name):
            env[name] = read_secret(name, env) or ""
            applied.append(name)
    return applied


def _credential_file_findings(env) -> list[Finding]:
    path = env.get("AUTH_CREDENTIALS_FILE")
    if not path:
        return []
    try:
        mode = Path(path).stat().st_mode
    except OSError:
        return []
    if mode & (stat.S_IRWXG | stat.S_IRWXO) and not path.startswith("/run/secrets/"):
        return [Finding("warn", "credentials_file_permissions", "資格情報のファイルが、所有者以外から読めます（600 にしてください）")]
    return []


def check_production(env=None) -> list[Finding]:
    """本番に出してよい設定かを検査する。値は結果に含めない。"""
    env = os.environ if env is None else env
    findings: list[Finding] = []
    add = lambda sev, code, msg: findings.append(Finding(sev, code, msg))  # noqa: E731

    try:
        credentials = read_secret("AUTH_CREDENTIALS", env)
    except ConfigError as e:
        credentials = None
        add("error", "auth_credentials_unreadable", str(e))
    if not credentials:
        add("error", "auth_missing", "認証の資格情報（AUTH_CREDENTIALS または AUTH_CREDENTIALS_FILE）がありません。認証なしでは、本番で動かせません")
    findings += _credential_file_findings(env)

    try:
        database_url = read_secret("DATABASE_URL", env)
    except ConfigError as e:
        database_url = None
        add("error", "database_url_unreadable", str(e))
    if not database_url:
        add("error", "database_missing", "DATABASE_URL がありません。本番では、SQLite のファイルを使いません")
    else:
        parsed = urlparse(database_url)
        password = parsed.password or ""
        if not password or password.lower() in WEAK_PASSWORDS or len(password) < MIN_PASSWORD_LENGTH:
            add("error", "database_weak_password", f"DB のパスワードが、空・既知の値・{MIN_PASSWORD_LENGTH}文字未満のいずれかです")
        sslmode = parse_qs(parsed.query).get("sslmode", [""])[0]
        if (parsed.hostname or "") not in LOCAL_HOSTS and sslmode not in TLS_MODES:
            add("warn", "database_no_tls", "DB への接続が、暗号化（sslmode=require 以上）されていません。同一のホスト・閉じたネットワークの内側だけで許されます")

    try:
        llm_key = read_secret("OPENAI_API_KEY", env)
    except ConfigError as e:
        llm_key = None
        add("error", "llm_key_unreadable", str(e))
    if not llm_key:
        add("error", "llm_key_missing", "LLM のキー（OPENAI_API_KEY または OPENAI_API_KEY_FILE）がありません")

    if (env.get("CREWAI_DISABLE_TELEMETRY") or "").lower() not in ("true", "1"):
        add("warn", "telemetry_enabled", "CrewAI のテレメトリが無効になっていません（CREWAI_DISABLE_TELEMETRY=true）。送信してよいかを、方針として決めてください")
    return findings


def enforce_production(env=None) -> list[Finding]:
    """APP_ENV=production なら検査し、error があれば ConfigError。warn は、返して、呼び出し側がログに出す。production 以外は、検査しない。"""
    env = os.environ if env is None else env
    if (env.get("APP_ENV") or "development").lower() != "production":
        return []
    findings = check_production(env)
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        raise ConfigError("本番に出せない設定です: " + "; ".join(f"[{f.code}] {f.message}" for f in errors))
    return findings


if __name__ == "__main__":  # python -m shared.config: 検査の結果を表示する（値は出さない）
    import sys
    result = check_production()
    for f in result:
        print(f"{f.severity.upper():5} [{f.code}] {f.message}")
    print("OK" if not any(f.severity == "error" for f in result) else "NG")
    sys.exit(1 if any(f.severity == "error" for f in result) else 0)
