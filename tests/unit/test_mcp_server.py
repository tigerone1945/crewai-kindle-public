"""付録K の検証用 MCP サーバーのテスト。Tool の関数を直接呼ぶ（プロセスも LLM も使わない）。サーバー側の検査は、Agent 側に頼らない。"""
import importlib.util

import pytest

from shared.review_requests import ReviewStore
from tests.helpers import ROOT


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("sales_mcp_server", ROOT / "examples" / "mcp" / "sales_mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_tool_returns_the_same_values_as_the_repository(server):
    out = server.sales_by_axis("region")
    assert "東北: 8,439,765円" in out and "上位からの累積" in out


@pytest.mark.parametrize("axis", ["customer_phone", "", "region; DROP TABLE x"])
def test_read_tool_refuses_axes_that_are_not_allowed(server, axis):
    with pytest.raises(ValueError):
        server.sales_by_axis(axis)


def test_write_tool_validates_on_the_server_side_and_registers_once(server, tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "r.db"))
    for bad in ("X1; DROP TABLE", "O9999"):
        with pytest.raises(ValueError):
            server.create_review_request(bad, "user-001")
    assert ReviewStore(tmp_path / "r.db").list_requests() == []  # 拒否された入力は、何も登録しない
    assert "登録しました" in server.create_review_request("O0001", "user-001")
    with pytest.raises(ValueError):
        server.create_review_request("O0001", "user-001")  # 重複
    assert [r["order_id"] for r in ReviewStore(tmp_path / "r.db").list_requests()] == ["O0001"]


# --- HTTP 版（Bearer 認証） ---

@pytest.fixture(scope="module")
def http_server():
    spec = importlib.util.spec_from_file_location("sales_mcp_http_server", ROOT / "examples" / "mcp" / "sales_mcp_http_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_server_rejects_missing_or_wrong_tokens_before_reaching_the_mcp_app(http_server):
    from starlette.testclient import TestClient
    client = TestClient(http_server.build_app("s3cret-token"), base_url="http://localhost:18765")  # MCP は、Host ヘッダーを検査する（DNS リバインディング対策）
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    for auth in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cret-token"}, {"Authorization": "Bearer s3cret-token "}):
        assert client.post("/mcp", json=body, headers={**headers, **auth}).status_code == 401
    with client:  # lifespan を起動する（MCP のセッション管理）
        assert client.post("/mcp", json=body, headers={**headers, "Authorization": "Bearer s3cret-token"}).status_code == 200
