"""分析サービス API の利用側（第14章）。UI など、別のプロセスから、契約（shared/api_contract.py）どおりに呼ぶ。

CrewAI に依存しない。失敗は ApiError にそろえる（通信の失敗、契約どおりのエラー、想定外の応答のいずれも）。
利用者へ見せてよい文は、ApiError.message だけ。
"""
import os
from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from shared.api_contract import (AnalysisRequest, AnalysisResponse, DecisionRequest, DecisionResponse, ErrorResponse, HistoryTurn,
                                 MeResponse, PendingReviewList, PreferenceCreate, PreferenceList)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
CLIENT_TIMEOUT_SECONDS = 170.0  # サービスの時間切れ（150秒）より、少し長くする。先にサービスが 504 を返せるように


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 0, run_id: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.run_id, self.retry_after = code, message, status, run_id, retry_after


class AnalysisApiClient:
    def __init__(self, base_url: str | None = None, *, http: httpx.Client | None = None, timeout: float = CLIENT_TIMEOUT_SECONDS,
                 token: str | None = None) -> None:
        # http を渡せば、テストで、通信なしにサービスへつなげる（FastAPI の TestClient は httpx.Client）
        self._http = http or httpx.Client(base_url=base_url or os.getenv("ANALYSIS_API_URL", DEFAULT_BASE_URL), timeout=timeout)
        self._token = token  # 資格情報。リクエストの Authorization ヘッダーだけに使う。ログにも、エラーにも出さない

    def __repr__(self) -> str:  # トークンを、表示やログに出さない
        return "AnalysisApiClient(token=<set>)" if self._token else "AnalysisApiClient()"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._token:
            kwargs["headers"] = {**kwargs.get("headers", {}), "Authorization": f"Bearer {self._token}"}
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise ApiError("timeout", "分析サービスの応答が時間内に返りませんでした。しばらくしてから、もう一度お試しください。") from None
        except httpx.HTTPError:
            raise ApiError("unreachable", "分析サービスに接続できませんでした。しばらくしてから、もう一度お試しください。") from None
        if response.is_success:
            return response
        try:
            error = ErrorResponse.model_validate(response.json()).error
        except (ValueError, ValidationError):  # 契約どおりでない応答（中継の障害ページなど）。本文は、利用者へ見せない
            raise ApiError("unknown", "分析サービスから想定外の応答がありました。", status=response.status_code) from None
        raise ApiError(error.code, error.message, status=response.status_code, run_id=error.run_id, retry_after=error.retry_after_seconds)

    def me(self) -> MeResponse:
        """呼び出している本人。認証が必要でトークンがなければ、ApiError(unauthenticated)。"""
        return MeResponse.model_validate(self._request("GET", "/v1/me").json())

    def analyze(self, question: str, actor_id: str, history: Sequence[HistoryTurn] = ()) -> AnalysisResponse:
        body = AnalysisRequest(question=question, actor_id=actor_id, history=list(history))
        return AnalysisResponse.model_validate(self._request("POST", "/v1/analyses", json=body.model_dump()).json())

    def pending_reviews(self):
        return PendingReviewList.model_validate(self._request("GET", "/v1/reviews/pending").json()).items

    def decide(self, flow_id: str, decision: str, approver_id: str) -> str:
        body = DecisionRequest(decision=decision, approver_id=approver_id)
        return DecisionResponse.model_validate(self._request("POST", f"/v1/reviews/{flow_id}/decision", json=body.model_dump()).json()).result

    def preferences(self, actor_id: str):
        return PreferenceList.model_validate(self._request("GET", f"/v1/actors/{actor_id}/preferences").json()).items

    def add_preference(self, actor_id: str, content: str) -> None:
        self._request("POST", f"/v1/actors/{actor_id}/preferences", json=PreferenceCreate(content=content).model_dump())

    def delete_preference(self, actor_id: str, record_id: str) -> None:
        self._request("DELETE", f"/v1/actors/{actor_id}/preferences/{record_id}")
