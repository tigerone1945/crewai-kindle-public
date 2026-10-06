"""API クライアント（第14章）のテスト。通信は、偽物のトランスポート（httpx.MockTransport）と、本物のサービス（TestClient）。"""
import httpx
import pytest
from fastapi.testclient import TestClient

import shared.api as api
from shared.analysis_flow import FlowOutcome
from shared.api_client import AnalysisApiClient, ApiError
from shared.api_contract import HistoryTurn
from shared.memory_policy import PreferenceStore
from shared.review_requests import ReviewStore
from shared.schemas import Metric, SalesAnalysisResult
from tests.fakes import FakeMemory
from tests.helpers import ROOT


def result():
    return SalesAnalysisResult(question="q", analysis_type="region_sales", summary="東北が最も高い売上です",
                               metrics=[Metric(label="東北の売上", value=8439765, unit="円")], insights=["a", "b"], recommendations=["c"])


@pytest.fixture
def service(tmp_path):
    outcome = {"value": FlowOutcome("analysis", "single", result(), attempts=1)}
    calls = []

    def run_question(question, actor_id, **kwargs):
        calls.append((question, kwargs))
        return outcome["value"]

    deps = api.Dependencies(review_store=ReviewStore(tmp_path / "r.db"), flow_db=tmp_path / "f.db", run_question=run_question,
                            preference_store_factory=lambda: PreferenceStore(FakeMemory()), trace_dir=tmp_path / "t")
    http = TestClient(api.create_app(deps), base_url="http://testserver")
    return type("S", (), {"client": AnalysisApiClient(http=http), "outcome": outcome, "calls": calls, "deps": deps})()


def test_analyze_returns_a_typed_response(service):
    r = service.client.analyze("地域別の売上を比較してください。", "user-001", [HistoryTurn(question="q", analysis_type="sales_summary", summary="s")])
    assert r.kind == "analysis" and r.result.summary == "東北が最も高い売上です" and r.run_id
    assert service.calls[0][1]["history"][0].summary == "s"


def test_contract_errors_become_api_errors_with_code_and_run_id(service):
    service.outcome["value"] = FlowOutcome("rejected", "rejected", message="この質問には対応できません。", reason="out_of_scope")
    with pytest.raises(ApiError) as e:
        service.client.analyze("Pythonでゲームを作って", "user-001")
    assert (e.value.code, e.value.status, e.value.message) == ("input_rejected", 422, "この質問には対応できません。") and e.value.run_id


def test_busy_and_unavailable_carry_retry_after(service):
    service.outcome["value"] = FlowOutcome("escalated", "single", message="時間をおいて", error_kind="transient")
    with pytest.raises(ApiError) as e:
        service.client.analyze("地域別の売上を比較してください。", "user-001")
    assert e.value.code == "unavailable" and e.value.retry_after == api.RETRY_AFTER_SECONDS


def test_the_client_refuses_to_send_a_request_that_breaks_the_contract(service):
    with pytest.raises(ValueError):
        service.client.analyze("", "user-001")  # サービスへ送る前に、契約で止める
    assert service.calls == []


def test_connection_failure_is_unreachable_without_leaking_details():
    def refuse(request):
        raise httpx.ConnectError("secret-host:5432 refused")

    client = AnalysisApiClient(http=httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x"))
    with pytest.raises(ApiError) as e:
        client.analyze("地域別の売上を比較してください。", "user-001")
    assert e.value.code == "unreachable" and "secret-host" not in e.value.message


def test_timeout_is_reported_as_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow")

    client = AnalysisApiClient(http=httpx.Client(transport=httpx.MockTransport(slow), base_url="http://x"))
    with pytest.raises(ApiError) as e:
        client.analyze("地域別の売上を比較してください。", "user-001")
    assert e.value.code == "timeout"


def test_a_non_contract_error_page_is_not_shown_to_the_user():
    client = AnalysisApiClient(http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>upstream secret-host</html>")),
                                                 base_url="http://x"))
    with pytest.raises(ApiError) as e:
        client.analyze("地域別の売上を比較してください。", "user-001")
    assert (e.value.code, e.value.status) == ("unknown", 502) and "secret-host" not in e.value.message


def test_preferences_round_trip(service):
    service.client.add_preference("user-001", "要約は結論から書く")
    [item] = service.client.preferences("user-001")
    assert item.content == "要約は結論から書く"
    service.client.delete_preference("user-001", item.id)
    assert service.client.preferences("user-001") == []
    with pytest.raises(ApiError) as e:
        service.client.add_preference("user-001", "今月の売上は1,234,567円")
    assert e.value.code == "policy_violation"


def test_client_and_result_view_do_not_import_crewai():
    """UI と API の分離: 利用側は、CrewAI を（間接的にも）読み込まない。新しいプロセスで、実際に import して確かめる。"""
    import subprocess
    import sys
    code = "import shared.api_client, shared.api_contract, shared.result_view, sys; sys.exit(1 if 'crewai' in sys.modules else 0)"
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-500:]
