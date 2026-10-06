"""資格情報の管理（第16章）のテスト。"""
import json
import stat

import pytest

from shared.auth import Authenticator, load_credentials
from tests.helpers import load_chapter_module

mc = load_chapter_module("ch16-production", "manage_credentials.py")


def test_create_returns_the_token_once_and_stores_only_its_digest(tmp_path, capsys):
    path = tmp_path / "creds.json"
    token = mc.create(path, "alice", "user-001", ["analyst"], 30)
    text = path.read_text(encoding="utf-8")
    assert token not in text and "token_sha256" in text
    creds = load_credentials(text)
    assert Authenticator(creds).authenticate(f"Bearer {token}").actor_id == "user-001"
    assert creds[0].expires_at is not None


def test_credentials_file_is_owner_only(tmp_path):
    path = tmp_path / "creds.json"
    mc.create(path, "alice", "user-001", ["analyst"], None)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_duplicate_ids_are_refused_and_the_file_is_unchanged(tmp_path):
    path = tmp_path / "creds.json"
    mc.create(path, "alice", "user-001", ["analyst"], None)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="すでに"):
        mc.create(path, "alice", "user-002", ["analyst"], None)
    assert path.read_text(encoding="utf-8") == before


def test_invalid_roles_or_actor_are_refused_and_nothing_is_written(tmp_path):
    path = tmp_path / "creds.json"
    for roles, actor in ((["root"], "user-001"), ([], "user-001"), (["analyst"], "bad id")):
        with pytest.raises(ValueError):
            mc.create(path, "x", actor, roles, None)
    assert not path.exists()


def test_revoke_removes_only_that_credential_and_the_token_stops_working(tmp_path):
    path = tmp_path / "creds.json"
    alice = mc.create(path, "alice", "user-001", ["analyst"], None)
    bob = mc.create(path, "bob", "user-002", ["analyst"], None)
    assert mc.revoke(path, "alice") is True and mc.revoke(path, "alice") is False
    auth = Authenticator(load_credentials(path.read_text(encoding="utf-8")))
    assert auth.authenticate(f"Bearer {bob}").actor_id == "user-002"
    with pytest.raises(Exception):
        auth.authenticate(f"Bearer {alice}")


def test_token_is_printed_to_stdout_only(tmp_path, capsys):
    path = tmp_path / "creds.json"
    assert mc.main(["--file", str(path), "create", "--id", "alice", "--actor", "user-001", "--roles", "analyst,approver"]) == 0
    out, err = capsys.readouterr()
    token = out.strip()
    assert len(token) >= 40 and token not in err
    assert mc.main(["--file", str(path), "list"]) == 0
    listing = capsys.readouterr().out
    assert "alice" in listing and token not in listing and "token_sha256" not in listing


def test_a_failed_write_keeps_the_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "creds.json"
    mc.create(path, "alice", "user-001", ["analyst"], None)
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(mc.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        mc.create(path, "bob", "user-002", ["analyst"], None)
    assert path.read_text(encoding="utf-8") == before and [p.name for p in tmp_path.iterdir()] == ["creds.json"]  # 一時ファイルも残らない
