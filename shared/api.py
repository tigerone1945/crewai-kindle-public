"""分析サービスの API（第14章）。FastAPI で、第13章の Flow（run_question）を、HTTP の Service として公開する。

起動: uv run uvicorn --factory shared.api:create_app --host 127.0.0.1 --port 8000

設計:
- 画面（UI）と分析を、別のプロセスにする。UI は、この API の契約（shared/api_contract.py）だけを知っていればよい。
- 分析（LLM を呼ぶ、数秒〜数十秒かかる同期の処理）は、イベントループを止めないよう、スレッドで実行する（`asyncio.to_thread`）。
  async の関数の中で直接呼ぶと、動きはするが、実行中は他のすべてのリクエストが待たされる（Spike s23: 1.5秒の処理で、/health の応答が1.3秒遅れた）。
- 同時に処理する分析の数に上限を置く（LLM のコストと、外部 API の制限を守る）。超えたら 429（busy）。
- 時間切れは 504。ただし、スレッドは途中で止められない。実行中の分析が終わるまで、枠は空けない（枠を空けると、上限を超えて実行される）。
- 失敗は、同じ形のエラー（ErrorResponse）で返す。内部のエラー文・入力の値は、返さない・ログにも出さない。
- 認証・認可は、この章の範囲外（第16章）。actor_id は、利用者を区別するための識別子であり、本人確認ではない。
  そのため、既定では 127.0.0.1 だけで待ち受ける。
"""
import asyncio
import functools
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import shared.analysis_flow as analysis_flow
import shared.memory_policy as memory_policy
import shared.review_flow as review_flow
import shared.review_requests as review_requests
from shared.api_contract import (AnalysisRequest, AnalysisResponse, DecisionRequest, DecisionResponse, ErrorBody, ErrorField,
                                 ErrorResponse, FlowId, PendingReview, PendingReviewList, PreferenceCreate, PreferenceItem,
                                 PreferenceList)
from shared.conversation import Turn
from shared.tracing import trace_run

logger = logging.getLogger("analysis_api")

MAX_CONCURRENT_ANALYSES = 4
REQUEST_TIMEOUT_SECONDS = 150.0  # LLM のタイムアウト（60秒）と、再試行（最大3回の作り直し + 一時的な失敗の再試行）を見込む
RETRY_AFTER_SECONDS = 5


class ApiError(Exception):
    """契約どおりのエラー応答にする例外。message は、利用者へ見せてよい文だけ。"""

    def __init__(self, status: int, code: str, message: str, *, run_id: str | None = None,
                 retry_after: int | None = None, fields: list[ErrorField] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.run_id = status, code, message, run_id
        self.retry_after, self.fields = retry_after, fields or []


def _error_response(e: ApiError) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=e.code, message=e.message, run_id=e.run_id,
                                         retry_after_seconds=e.retry_after, fields=e.fields))
    headers = {"Retry-After": str(e.retry_after)} if e.retry_after else {}
    if e.run_id:
        headers["X-Run-Id"] = e.run_id
    return JSONResponse(body.model_dump(mode="json"), status_code=e.status, headers=headers)


@dataclass
class Dependencies:
    """サービスが使うもの。テストでは、偽物に差し替える。"""

    review_store: review_requests.ReviewStore = field(default_factory=lambda: review_requests.ReviewStore(review_requests.DEFAULT_DB))
    flow_db: Path = review_flow.DEFAULT_FLOW_DB
    preference_store_factory: Callable[[], memory_policy.PreferenceStore] = memory_policy.default_preference_store
    run_question: Callable = analysis_flow.run_question
    resolve_review: Callable = review_flow.resolve_review
    max_concurrent: int = MAX_CONCURRENT_ANALYSES
    request_timeout: float = REQUEST_TIMEOUT_SECONDS
    trace_dir: Path | None = None  # None なら、既定の保存先（RunTrace.save の既定）

    def __post_init__(self) -> None:
        self.preference_store = functools.cache(self.preference_store_factory)  # 初回に使うときに作る（起動を重くしない）


# 契約でのステータスとコード。outcome の種類（FlowOutcome.kind）から、HTTP へ変える
_REJECTION_STATUS = 422


def _outcome_to_response(outcome, run_id: str, response: Response) -> AnalysisResponse:
    if outcome.kind == "analysis":
        return AnalysisResponse(run_id=run_id, kind="analysis", route=outcome.route, attempts=outcome.attempts, result=outcome.result)
    if outcome.kind == "review_pending":
        response.status_code = 202  # 受け付けたが、完了していない（承認待ち）
        return AnalysisResponse(run_id=run_id, kind="review_pending", route=outcome.route, message=outcome.message,
                                review_flow_id=outcome.review_flow_id)
    if outcome.kind == "rejected":
        raise ApiError(_REJECTION_STATUS, "input_rejected", outcome.message, run_id=run_id)
    if outcome.kind == "review_invalid":
        raise ApiError(_REJECTION_STATUS, "review_invalid", outcome.message, run_id=run_id)
    if outcome.kind == "escalated" and outcome.error_kind == "transient":
        raise ApiError(503, "unavailable", outcome.message, run_id=run_id, retry_after=RETRY_AFTER_SECONDS)
    if outcome.kind == "escalated":
        raise ApiError(502, "analysis_failed", outcome.message, run_id=run_id)
    raise ApiError(500, "internal", "処理中にエラーが発生しました。", run_id=run_id)  # 想定外の kind


def create_app(deps: Dependencies | None = None) -> FastAPI:
    deps = deps or Dependencies()
    app = FastAPI(title="売上分析サービス", version="1", docs_url="/docs", redoc_url=None)
    app.state.deps = deps
    app.state.running = 0  # 実行中の分析の数。イベントループのスレッドだけが増減する（ロック不要）

    # --- エラー: すべて ErrorResponse の形にする ---

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _error_response(e)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError):
        # FastAPI の既定は、入力の値（input）を応答に含める。質問に秘密情報が入っていると、反射して返してしまう。値は返さない。
        fields = [ErrorField(field=".".join(str(p) for p in err["loc"][1:]) or "body", problem=err["msg"]) for err in e.errors()]
        return _error_response(ApiError(422, "invalid_request", "リクエストの形式が正しくありません。", fields=fields))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        logger.error("unhandled error type=%s", type(e).__name__)  # 内容は出さない（入力や内部の情報を含みうる）
        return _error_response(ApiError(500, "internal", "処理中にエラーが発生しました。"))

    # --- 分析 ---

    def _analyze_blocking(req: AnalysisRequest, run_id: str):
        """スレッドで動く、同期の処理。Trace を、この実行だけに束縛して残す。"""
        history = [Turn(h.question, h.analysis_type, h.summary) for h in req.history]
        with trace_run(run_id) as trace:
            try:
                return deps.run_question(
                    req.question, req.actor_id, review_store=deps.review_store, flow_db=deps.flow_db, history=history,
                    preferences_for=lambda q: deps.preference_store().recall_preferences(req.actor_id, q))
            finally:
                try:
                    trace.save(deps.trace_dir) if deps.trace_dir else trace.save()
                except OSError:
                    logger.exception("trace save failed run_id=%s", run_id)  # 観測の失敗で、利用者への回答を止めない

    @app.post("/v1/analyses", response_model=AnalysisResponse, response_model_exclude_none=True,
              responses={s: {"model": ErrorResponse} for s in (422, 429, 500, 502, 503, 504)})
    async def create_analysis(req: AnalysisRequest, response: Response):
        run_id = uuid.uuid4().hex[:12]
        response.headers["X-Run-Id"] = run_id
        if app.state.running >= deps.max_concurrent:
            raise ApiError(429, "busy", "ただいま混み合っています。しばらくしてから、もう一度お試しください。",
                           run_id=run_id, retry_after=RETRY_AFTER_SECONDS)
        app.state.running += 1
        task = asyncio.ensure_future(asyncio.to_thread(_analyze_blocking, req, run_id))

        def _finished(t: asyncio.Future) -> None:
            app.state.running -= 1  # スレッドが実際に終わってから、枠を空ける（時間切れでも、実行中は枠を占める）
            if not t.cancelled() and t.exception() is not None:
                logger.error("analysis failed run_id=%s type=%s", run_id, type(t.exception()).__name__)

        task.add_done_callback(_finished)
        try:
            outcome = await asyncio.wait_for(asyncio.shield(task), timeout=deps.request_timeout)
        except asyncio.TimeoutError:
            raise ApiError(504, "timeout", "処理に時間がかかっています。しばらくしてから、もう一度お試しください。", run_id=run_id) from None
        except Exception:
            raise ApiError(500, "internal", "処理中にエラーが発生しました。", run_id=run_id) from None
        return _outcome_to_response(outcome, run_id, response)

    # --- 承認（第8章）: 同期の関数は、FastAPI がスレッドで実行する ---

    @app.get("/v1/reviews/pending", response_model=PendingReviewList)
    def pending_reviews():
        items = []
        for p in deps.review_store.pending_approvals():
            order, problem = None, None
            try:
                order = review_requests.validate_review_target(p["order_id"])
            except review_requests.InvalidReviewTarget as e:
                problem = e.message
            items.append(PendingReview(flow_id=p["flow_id"], order_id=p["order_id"], requested_by=p["requested_by"],
                                       order=order, problem=problem))
        return PendingReviewList(items=items)

    @app.post("/v1/reviews/{flow_id}/decision", response_model=DecisionResponse,
              responses={s: {"model": ErrorResponse} for s in (403, 409, 422)})
    def decide_review(flow_id: FlowId, req: DecisionRequest):
        try:
            result = deps.resolve_review(flow_id, req.decision, req.approver_id, deps.review_store, deps.flow_db)
        except review_flow.SelfApprovalError as e:
            raise ApiError(403, "self_approval", str(e)) from None
        except ValueError:  # 保留がない（すでに他の承認者が決めた、または存在しない）
            raise ApiError(409, "already_resolved", "この依頼は、すでに処理されているか、存在しません。") from None
        return DecisionResponse(result=result)

    # --- 好み（第5章） ---

    @app.get("/v1/actors/{actor_id}/preferences", response_model=PreferenceList)
    def list_preferences(actor_id: str):
        _check_actor(actor_id)
        return PreferenceList(items=[PreferenceItem(id=i, content=c) for i, c in deps.preference_store().list_preferences(actor_id)])

    @app.post("/v1/actors/{actor_id}/preferences", status_code=201, response_model=PreferenceList,
              responses={422: {"model": ErrorResponse}})
    def add_preference(actor_id: str, req: PreferenceCreate):
        _check_actor(actor_id)
        try:
            deps.preference_store().remember_preference(actor_id, req.content)
        except memory_policy.PolicyViolation as e:
            raise ApiError(422, "policy_violation", str(e)) from None
        return PreferenceList(items=[PreferenceItem(id=i, content=c) for i, c in deps.preference_store().list_preferences(actor_id)])

    @app.delete("/v1/actors/{actor_id}/preferences/{record_id}", status_code=204)
    def delete_preference(actor_id: str, record_id: str):
        _check_actor(actor_id)
        deps.preference_store().delete_preference(actor_id, record_id)
        return Response(status_code=204)

    @app.get("/healthz")
    async def healthz():
        """生存の確認。LLM・DB には触れない（外部の障害で、プロセスが落ちたと誤判定しないため）。"""
        return {"status": "ok", "running": app.state.running}

    return app


def _check_actor(actor_id: str) -> None:
    try:
        memory_policy.check_actor_id(actor_id)
    except ValueError as e:
        raise ApiError(422, "invalid_request", str(e)) from None
