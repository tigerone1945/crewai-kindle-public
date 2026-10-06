"""認証・認可（第16章）のテスト。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from shared.auth import (AuthError, Authenticator, Credential, credential_from_dict, credential_to_dict, hash_token, load_credentials,
                         new_credential)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def auth():
    token_a, cred_a = new_credential("cred-analyst", "user-001", ["analyst"])
    token_b, cred_b = new_credential("cred-approver", "approver-001", ["approver", "analyst"], expires_at=NOW + timedelta(days=1))
    token_c, cred_c = new_credential("cred-expired", "user-002", ["analyst"], expires_at=NOW - timedelta(seconds=1))
    return type("A", (), {"auth": Authenticator([cred_a, cred_b, cred_c], clock=lambda: NOW), "analyst": token_a, "approver": token_b, "expired": token_c})()


def bearer(token):
    return f"Bearer {token}"


def test_valid_token_gives_the_identity_bound_to_the_credential(auth):
    ident = auth.auth.authenticate(bearer(auth.analyst))
    assert (ident.credential_id, ident.actor_id, ident.roles) == ("cred-analyst", "user-001", frozenset({"analyst"}))


def test_scheme_is_case_insensitive_and_whitespace_is_tolerated(auth):
    assert auth.auth.authenticate(f"bearer   {auth.analyst} ").actor_id == "user-001"


@pytest.mark.parametrize("header, reason", [(None, "missing_header"), ("", "missing_header"), ("Basic abc", "malformed_header"),
                                            ("Bearer", "malformed_header"), ("Bearer    ", "malformed_header"), ("Bearer wrong", "unknown_token")])
def test_bad_credentials_are_unauthenticated(auth, header, reason):
    with pytest.raises(AuthError) as e:
        auth.auth.authenticate(header)
    assert e.value.code == "unauthenticated" and e.value.reason == reason


def test_expired_token_is_rejected(auth):
    with pytest.raises(AuthError) as e:
        auth.auth.authenticate(bearer(auth.expired))
    assert e.value.code == "unauthenticated" and e.value.reason.startswith("expired")


def test_revoking_a_credential_means_removing_it(auth):
    remaining = [c for c in auth.auth.credentials if c.id != "cred-analyst"]
    with pytest.raises(AuthError):
        Authenticator(remaining, clock=lambda: NOW).authenticate(bearer(auth.analyst))


def test_authorization_distinguishes_missing_role_from_actor_mismatch(auth):
    ident = auth.auth.authenticate(bearer(auth.analyst))
    ident.require_role("analyst")
    ident.require_actor("user-001")
    with pytest.raises(AuthError) as role:
        ident.require_role("approver")
    with pytest.raises(AuthError) as actor:
        ident.require_actor("user-999")
    assert (role.value.code, actor.value.code) == ("forbidden", "forbidden") and role.value.reason != actor.value.reason


def test_only_the_digest_is_kept_and_it_is_not_the_token(auth):
    for c in auth.auth.credentials:
        assert len(c.token_sha256) == 64 and c.token_sha256 != hash_token("") and auth.analyst not in json.dumps(credential_to_dict(c))


def test_tokens_are_unique_and_long():
    tokens = {new_credential(f"c{i}", "user-001", ["analyst"])[0] for i in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 40 for t in tokens)


def test_credentials_round_trip_through_json(auth):
    text = json.dumps([credential_to_dict(c) for c in auth.auth.credentials])
    assert [credential_to_dict(c) for c in load_credentials(text)] == [credential_to_dict(c) for c in auth.auth.credentials]


@pytest.mark.parametrize("patch, message", [
    ({"roles": []}, "roles"), ({"roles": ["root"]}, "roles"), ({"token_sha256": "abc"}, "token_sha256"),
    ({"token_sha256": "Z" * 64}, "token_sha256"), ({"actor_id": "bad id"}, "actor_id"),
    ({"expires_at": "2026-10-05T12:00:00"}, "タイムゾーン"),
])
def test_invalid_credential_files_are_rejected_at_load_time(auth, patch, message):
    entry = credential_to_dict(auth.auth.credentials[0]) | patch
    with pytest.raises(ValueError, match=message):
        credential_from_dict(entry)


def test_duplicate_credential_ids_are_rejected(auth):
    entry = credential_to_dict(auth.auth.credentials[0])
    with pytest.raises(ValueError, match="重複"):
        load_credentials(json.dumps([entry, entry]))


def test_new_credential_validates_roles_and_actor():
    with pytest.raises(ValueError):
        new_credential("c", "user-001", [])
    with pytest.raises(ValueError):
        new_credential("c", "user-001", ["admin"])
    with pytest.raises(ValueError):
        new_credential("c", "bad id", ["analyst"])
