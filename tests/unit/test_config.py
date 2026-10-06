"""設定・秘密情報・Production Readiness（第16章）のテスト。"""
import json

import pytest

from shared.config import ConfigError, apply_secret_files, check_production, enforce_production, read_secret

GOOD_URL = "postgresql://app:" + "x" * 20 + "@db:5432/sales"
GOOD = {"APP_ENV": "production", "AUTH_CREDENTIALS": "[]", "DATABASE_URL": GOOD_URL, "OPENAI_API_KEY": "sk-test-0000",
        "CREWAI_DISABLE_TELEMETRY": "true"}


def codes(env, severity=None):
    return {f.code for f in check_production(env) if severity in (None, f.severity)}


# --- 秘密情報の受け取り ---

def test_secret_from_environment_or_file(tmp_path):
    (tmp_path / "key").write_text("sk-from-file\n")
    assert read_secret("OPENAI_API_KEY", {"OPENAI_API_KEY": "sk-env"}) == "sk-env"
    assert read_secret("OPENAI_API_KEY", {"OPENAI_API_KEY_FILE": str(tmp_path / "key")}) == "sk-from-file"  # 末尾の改行は除く
    assert read_secret("OPENAI_API_KEY", {}) is None


def test_both_forms_with_different_values_are_ambiguous_and_refused(tmp_path):
    (tmp_path / "key").write_text("x")
    with pytest.raises(ConfigError, match="両方"):
        read_secret("OPENAI_API_KEY", {"OPENAI_API_KEY": "a", "OPENAI_API_KEY_FILE": str(tmp_path / "key")})


def test_both_forms_with_the_same_value_are_allowed_because_startup_exports_the_file(tmp_path):
    """起動時に、ファイルの値を環境変数へ出す（apply_secret_files）。そのあとの検査が、衝突と誤判定しない。"""
    (tmp_path / "key").write_text("sk-same\n")
    env = {"OPENAI_API_KEY_FILE": str(tmp_path / "key")}
    apply_secret_files(env)
    assert read_secret("OPENAI_API_KEY", env) == "sk-same" and codes(GOOD | env, "error") == set()


def test_unreadable_or_empty_secret_file_fails_without_leaking_the_path_content(tmp_path):
    (tmp_path / "empty").write_text("  \n")
    with pytest.raises(ConfigError, match="空"):
        read_secret("OPENAI_API_KEY", {"OPENAI_API_KEY_FILE": str(tmp_path / "empty")})
    with pytest.raises(ConfigError, match="読めません"):
        read_secret("OPENAI_API_KEY", {"OPENAI_API_KEY_FILE": str(tmp_path / "missing")})


def test_apply_secret_files_exports_values_for_libraries_that_read_the_variable(tmp_path):
    (tmp_path / "key").write_text("sk-from-file")
    env = {"OPENAI_API_KEY_FILE": str(tmp_path / "key")}
    assert apply_secret_files(env) == ["OPENAI_API_KEY"] and env["OPENAI_API_KEY"] == "sk-from-file"


def test_apply_secret_files_does_not_override_an_explicit_variable(tmp_path):
    (tmp_path / "key").write_text("sk-from-file")
    env = {"OPENAI_API_KEY": "sk-explicit", "OPENAI_API_KEY_FILE": str(tmp_path / "key")}
    assert apply_secret_files(env) == [] and env["OPENAI_API_KEY"] == "sk-explicit"


# --- Production Readiness ---

def test_a_complete_production_configuration_has_no_errors():
    assert codes(GOOD, "error") == set()


@pytest.mark.parametrize("remove, code", [("AUTH_CREDENTIALS", "auth_missing"), ("DATABASE_URL", "database_missing"),
                                          ("OPENAI_API_KEY", "llm_key_missing")])
def test_missing_essentials_are_errors(remove, code):
    env = {k: v for k, v in GOOD.items() if k != remove}
    assert code in codes(env, "error")


@pytest.mark.parametrize("password", ["", "devpassword", "Password", "short", "x" * 15])
def test_weak_database_passwords_are_errors(password):
    env = GOOD | {"DATABASE_URL": f"postgresql://app:{password}@db:5432/sales"}
    assert "database_weak_password" in codes(env, "error")


def test_database_without_tls_is_a_warning_for_remote_hosts_only():
    assert "database_no_tls" in codes(GOOD, "warn")
    assert "database_no_tls" not in codes(GOOD | {"DATABASE_URL": GOOD_URL + "?sslmode=verify-full"})
    assert "database_no_tls" not in codes(GOOD | {"DATABASE_URL": GOOD_URL.replace("@db:", "@127.0.0.1:")})
    assert "database_no_tls" in codes(GOOD | {"DATABASE_URL": GOOD_URL + "?sslmode=prefer"})  # prefer は、暗号化を保証しない


def test_telemetry_enabled_is_a_warning():
    env = {k: v for k, v in GOOD.items() if k != "CREWAI_DISABLE_TELEMETRY"}
    assert "telemetry_enabled" in codes(env, "warn")


def test_findings_never_contain_secret_values():
    env = GOOD | {"DATABASE_URL": "postgresql://app:devpassword@db:5432/sales", "OPENAI_API_KEY": "sk-super-secret-value"}
    text = json.dumps([f.__dict__ for f in check_production(env)], ensure_ascii=False)
    assert "devpassword" not in text and "sk-super-secret-value" not in text


def test_enforce_stops_startup_on_errors_and_reports_codes_only():
    with pytest.raises(ConfigError) as e:
        enforce_production({"APP_ENV": "production", "DATABASE_URL": "postgresql://app:devpassword@db/sales"})
    assert "auth_missing" in str(e.value) and "devpassword" not in str(e.value)


def test_enforce_returns_warnings_when_the_configuration_is_acceptable():
    findings = enforce_production(GOOD)
    assert findings and all(f.severity == "warn" for f in findings)


def test_enforce_does_nothing_outside_production():
    assert enforce_production({}) == [] and enforce_production({"APP_ENV": "development"}) == []


def test_credential_file_with_open_permissions_is_flagged(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("[]")
    path.chmod(0o644)
    assert "credentials_file_permissions" in codes(GOOD | {"AUTH_CREDENTIALS_FILE": str(path)}, "warn")
    path.chmod(0o600)
    assert "credentials_file_permissions" not in codes(GOOD | {"AUTH_CREDENTIALS_FILE": str(path)})


def test_database_url_can_come_from_a_file(tmp_path, monkeypatch):
    """Docker secrets は、ファイルで渡される。コンテナの起動で、DATABASE_URL_FILE だけが設定されていても、接続先が決まる。"""
    from shared.postgres import database_url
    (tmp_path / "db").write_text("postgresql://app:pw@db:5432/sales\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL_FILE", str(tmp_path / "db"))
    assert database_url() == "postgresql://app:pw@db:5432/sales"
    monkeypatch.delenv("DATABASE_URL_FILE")
    assert database_url() is None


def test_read_number_uses_the_default_when_unset_and_refuses_bad_values():
    from shared.config import read_number
    assert read_number("X", 14, env={}) == 14 and read_number("X", 14, env={"X": ""}) == 14
    assert read_number("X", 14, env={"X": "7.5"}) == 7.5
    for bad in ("abc", "-1", "nan"):
        with pytest.raises(ConfigError):
            read_number("X", 14, minimum=0, env={"X": bad})
    with pytest.raises(ConfigError) as e:
        read_number("X", 14, env={"X": "secret-looking-value"})
    assert "secret-looking-value" not in str(e.value)  # 値そのものは、メッセージに出さない
