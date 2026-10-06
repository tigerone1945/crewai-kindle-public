"""分析サービスの API（第14章）。FastAPI で、第13章の Flow（run_question）を、HTTP の Service として公開する。

起動: uv run uvicorn --factory shared.api:create_app --host 127.0.0.1 --port 8000

設計:
- 画面（UI）と分析を、別のプロセスにする。UI は、この API の契約（shared/api_contract.py）だけを知っていればよい。
- 分析（LLM を呼ぶ、数秒〜数十秒かかる同期の処理）は、イベントループを止めないよう、スレッドで実行する（`asyncio.to_thread`）。
  async の関数の中で直接呼ぶと、動きはするが、実行中は他のすべてのリクエストが待たされる（Spike s23: 1.5秒の処理で、/health の応答が1.3秒遅れた）。
- 同時に処理する分析の数に上限を置く（LLM のコストと、外部 API の制限を守る）。超えたら 429（busy）。
- 時間切れは 504。ただし、スレッドは途中で止められない。実行中の分析が終わるまで、枠は空けない（枠を空けると、上限を超えて実行される）。
- 失敗は、同じ形のエラー（ErrorResponse）で返す。内部のエラー文・入力の値は、返さない・ログにも出さない。
- 認証・認可（第16章）: authenticator があれば、すべての /v1 に、トークンを要求する。ID は、資格情報から決まる（リクエストが名乗る ID は、一致を確かめるだけ）。
  authenticator がなければ（開発用）、第14章までの動き。actor_id は、自己申告であり、本人確認ではない。そのため、127.0.0.1 だけで待ち受ける。
"""
import asyncio
import functools
import json
import logging
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from crewai.flow.persistence.base import FlowPersistence
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST

import shared.alerts as alerts
import shared.analysis_flow as analysis_flow
import shared.config as config
import shared.memory_policy as memory_policy
import shared.review_flow as review_flow
import shared.review_requests as review_requests
from shared.api_contract import (AnalysisRequest, AnalysisResponse, DecisionRequest, DecisionResponse, ErrorBody, ErrorField,
                                 ErrorResponse, FlowId, MeResponse, OpsStatus, PendingReview, PendingReviewList, PreferenceCreate, PreferenceItem,
                                 PreferenceList)
from shared.auth import AuthError, Authenticator, Identity, load_credentials
from shared.conversation import Turn
from shared.observability import Observer, build_run_record, configure_run_logging
from shared.tracing import TRACE_DIR, prune_traces, trace_run

logger = logging.getLogger("analysis_api")
class _StdoutHandler(logging.StreamHandler):
    """標準出力へ、メッセージだけを書く（1行1件の JSON。コンテナでは、標準出力が、ログの収集先）。出力先は、書くときに決める。"""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_audit_logging() -> None:
    """アクセスの記録（audit）を、標準出力へ出す。設定しないと、Python の既定（WARNING 以上）で、INFO の記録は、どこにも出ない。"""
    audit = logging.getLogger("audit")
    if not any(isinstance(h, _StdoutHandler) for h in audit.handlers):
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    audit.propagate = False  # 親（root）へも流すと、二重に出る


audit_logger = logging.getLogger("audit")  # 誰が・何を・結果はどうだったか。Trace（どう動いたか）とも、承認の監査ログ（業務上の決定）とも別

MAX_CONCURRENT_ANALYSES = 4
REQUEST_TIMEOUT_SECONDS = 150.0  # LLM のタイムアウト（60秒）と、再試行（最大3回の作り直し + 一時的な失敗の再試行）を見込む
RETRY_AFTER_SECONDS = 5
PRUNE_INTERVAL_SECONDS = 3600


class ApiError(Exception):
    """契約どおりのエラー応答にする例外。message は、利用者へ見せてよい文だけ。"""

    def __init__(self, status: int, code: str, message: str, *, run_id: str | None = None,
                 retry_after: int | None = None, fields: list[ErrorField] | None = None, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.run_id = status, code, message, run_id
        self.retry_after, self.fields, self.headers = retry_after, fields or [], headers or {}


def _error_response(e: ApiError) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=e.code, message=e.message, run_id=e.run_id,
                                         retry_after_seconds=e.retry_after, fields=e.fields))
    headers = {"Retry-After": str(e.retry_after)} if e.retry_after else {}
    headers.update(e.headers)
    if e.run_id:
        headers["X-Run-Id"] = e.run_id
    return JSONResponse(body.model_dump(mode="json"), status_code=e.status, headers=headers)


@dataclass
class Dependencies:
    """サービスが使うもの。テストでは、偽物に差し替える。"""

    review_store: review_requests.ReviewStore = field(default_factory=review_requests.default_review_store)  # DATABASE_URL があれば PostgreSQL
    flow_db: Path | FlowPersistence = field(default_factory=review_flow.default_flow_persistence)  # パス（SQLite）または永続化の実装
    preference_store_factory: Callable[[], memory_policy.PreferenceStore] = memory_policy.default_preference_store
    run_question: Callable = analysis_flow.run_question
    resolve_review: Callable = review_flow.resolve_review
    readiness_check: Callable[[], None] | None = None  # 依存先（DB）に届くか。失敗したら例外。None なら、既定の確認（DATABASE_URL があれば DB）
    max_concurrent: int = MAX_CONCURRENT_ANALYSES
    request_timeout: float = REQUEST_TIMEOUT_SECONDS
    trace_dir: Path | None = None  # None なら、既定の保存先（RunTrace.save の既定）
    authenticator: Authenticator | None = None  # None なら、認証なし（開発用）。本番は、必ず設定する（shared/config.py の検査）
    observer: Observer = field(default_factory=Observer)  # 実行の記録の振り分け先（Log・Metric・Alert の窓。第17章）
    alert_rules: tuple[alerts.AlertRule, ...] = alerts.DEFAULT_RULES
    alert_window_seconds: int = alerts.DEFAULT_WINDOW_SECONDS
    trace_retention_days: float = 14  # Trace を残す期間と上限。超えたものは消す（書き続けると、ディスクを埋める）
    trace_max_files: int = 5000

    def __post_init__(self) -> None:
        self.preference_store = functools.cache(self.preference_store_factory)  # 初回に使うときに作る（起動を重くしない）
        self.alert_tracker = alerts.AlertTracker()


    @classmethod
    def from_environment(cls, **overrides) -> "Dependencies":
        """環境から組み立てる（本番の起動）。秘密情報のファイルを読み、APP_ENV=production なら、設定を検査してから、資格情報を読む。"""
        for finding in config.enforce_production():  # error があれば、ここで ConfigError（起動しない）。ファイルの秘密情報を、環境へ出す前に検査する
            logger.warning("production readiness: [%s] %s", finding.code, finding.message)
        credentials = config.read_secret("AUTH_CREDENTIALS")
        config.apply_secret_files()  # LLM のライブラリなどが読む環境変数へ、ファイルの値を入れる（検査と資格情報の読み込みのあと）
        settings = {"trace_retention_days": config.read_number("TRACE_RETENTION_DAYS", 14, minimum=0.01),
                    "trace_max_files": int(config.read_number("TRACE_MAX_FILES", 5000, minimum=1)),
                    "alert_window_seconds": int(config.read_number("ALERT_WINDOW_SECONDS", alerts.DEFAULT_WINDOW_SECONDS, minimum=60))}
        return cls(authenticator=Authenticator(load_credentials(credentials)) if credentials else None, **{**settings, **overrides})


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
    deps = deps or Dependencies.from_environment()
    configure_audit_logging()
    configure_run_logging()
    alerts.configure_alert_logging()
    # 認証があるとき、API の仕様（OpenAPI）は公開しない（攻撃者に、操作の一覧を渡さない）
    docs = None if deps.authenticator else "/docs"
    app = FastAPI(title="売上分析サービス", version="1", docs_url=docs, redoc_url=None, openapi_url="/openapi.json" if docs else None)
    app.state.deps = deps
    app.state.last_prune = -PRUNE_INTERVAL_SECONDS  # 最初の実行で、1回走らせる
    app.state.running = 0  # 実行中の分析の数。イベントループのスレッドだけが増減する（ロック不要）
    deps.observer.in_flight.set_function(lambda: app.state.running)  # 読むときの値を返す（増減の記録の漏れで、ずれない）

    def alert_window() -> alerts.Window:
        since = time.time() - deps.alert_window_seconds
        return alerts.Window(deps.observer.runs_since(since), deps.observer.requests_since(since), _review_pending())

    def _review_pending() -> int | None:
        try:
            return len(deps.review_store.pending_approvals())
        except Exception:
            logger.exception("pending review count failed")  # 観測の失敗で、観測そのものを止めない（この指標だけ、欠ける）
            return None

    # --- 認証・認可（第16章）---

    def authorize(request: Request, *, role: str | None = None, actor_id: str | None = None) -> Identity | None:
        """認証して、役割と本人を確かめる。認証が無効（開発用）なら、何もしない。失敗は、理由を区別せず、決まった文で返す。"""
        if deps.authenticator is None:
            return None
        try:
            identity = deps.authenticator.authenticate(request.headers.get("authorization"))
            request.state.identity = identity
            if role:
                identity.require_role(role)
            if actor_id is not None:
                identity.require_actor(actor_id)
            return identity
        except AuthError as e:
            request.state.auth_reason = e.reason  # ログのためだけ。利用者へは返さない
            if e.code == "unauthenticated":
                raise ApiError(401, "unauthenticated", "認証が必要です。", headers={"WWW-Authenticate": "Bearer"}) from None
            raise ApiError(403, "forbidden", "この操作は許可されていません。") from None

    @app.middleware("http")
    async def access_audit(request: Request, call_next):
        """アクセスを、数える（全部）。/v1 へのアクセスは、構造化して記録する。トークン・質問・本文は、記録しない。"""
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")  # 実際のパスではなく、テンプレート（ラベルの組み合わせを増やさない）
        deps.observer.record_request(request.method, route, response.status_code, time.perf_counter() - started)
        if request.url.path.startswith("/v1/"):
            identity = getattr(request.state, "identity", None)
            audit_logger.info(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": "api_access", "method": request.method, "route": route, "status": response.status_code,
                "credential_id": identity.credential_id if identity else None, "actor_id": identity.actor_id if identity else None,
                "reason": getattr(request.state, "auth_reason", None), "run_id": response.headers.get("X-Run-Id"),
            }, ensure_ascii=False))
        return response

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
        """スレッドで動く、同期の処理。Trace を、この実行だけに束縛して残し、終わったら、運用の記録（Log・Metric）にする。"""
        history = [Turn(h.question, h.analysis_type, h.summary) for h in req.history]
        outcome, trace = None, None
        try:
            with trace_run(run_id) as trace:
                outcome = deps.run_question(
                    req.question, req.actor_id, review_store=deps.review_store, flow_db=deps.flow_db, history=history,
                    preferences_for=lambda q: deps.preference_store().recall_preferences(req.actor_id, q))
                return outcome
        finally:
            _finish_run(run_id, outcome, trace)  # with を出たあと（Event の取り込みが済んでから）。例外のときも

    prune_lock = threading.Lock()

    def _prune_traces() -> None:
        """1時間に1回、古い Trace を消す。実行のたびには走らせない。"""
        now = time.monotonic()
        with prune_lock:
            if now - app.state.last_prune < PRUNE_INTERVAL_SECONDS:
                return
            app.state.last_prune = now
        removed = prune_traces(deps.trace_dir or TRACE_DIR, max_age_days=deps.trace_retention_days, max_files=deps.trace_max_files)
        if removed:
            logger.info("pruned %d old traces", removed)

    def _finish_run(run_id: str, outcome, trace) -> None:
        """Trace の保存と、運用の記録。どちらの失敗も、利用者への回答を止めない。"""
        try:
            trace.save(deps.trace_dir) if deps.trace_dir else trace.save()
            _prune_traces()
        except OSError:
            logger.exception("trace save failed run_id=%s", run_id)
        try:
            deps.observer.record_run(build_run_record(run_id, outcome, trace))
            deps.alert_tracker.update(alerts.evaluate(alert_window(), deps.alert_rules))
        except Exception:
            logger.exception("run record failed run_id=%s", run_id)

    @app.post("/v1/analyses", response_model=AnalysisResponse, response_model_exclude_none=True,
              responses={s: {"model": ErrorResponse} for s in (401, 403, 422, 429, 500, 502, 503, 504)})
    async def create_analysis(req: AnalysisRequest, request: Request, response: Response):
        authorize(request, role="analyst", actor_id=req.actor_id)  # 他人の名前で、分析させない
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

    @app.get("/v1/me", response_model=MeResponse)
    def me(request: Request):
        identity = authorize(request)
        if identity is None:
            return MeResponse(auth_required=False)
        return MeResponse(auth_required=True, actor_id=identity.actor_id, roles=sorted(identity.roles), credential_id=identity.credential_id)

    @app.get("/v1/reviews/pending", response_model=PendingReviewList)
    def pending_reviews(request: Request):
        authorize(request, role="approver")
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
    def decide_review(flow_id: FlowId, req: DecisionRequest, request: Request):
        authorize(request, role="approver", actor_id=req.approver_id)  # 承認者は、認証された本人。他人の名前で承認させない
        try:
            result = deps.resolve_review(flow_id, req.decision, req.approver_id, deps.review_store, deps.flow_db)
        except review_flow.SelfApprovalError as e:
            raise ApiError(403, "self_approval", str(e)) from None
        except ValueError:  # 保留がない（すでに他の承認者が決めた、または存在しない）
            raise ApiError(409, "already_resolved", "この依頼は、すでに処理されているか、存在しません。") from None
        return DecisionResponse(result=result)

    # --- 好み（第5章） ---

    @app.get("/v1/actors/{actor_id}/preferences", response_model=PreferenceList)
    def list_preferences(actor_id: str, request: Request):
        _check_actor(actor_id)
        authorize(request, role="analyst", actor_id=actor_id)
        return PreferenceList(items=[PreferenceItem(id=i, content=c) for i, c in deps.preference_store().list_preferences(actor_id)])

    @app.post("/v1/actors/{actor_id}/preferences", status_code=201, response_model=PreferenceList,
              responses={422: {"model": ErrorResponse}})
    def add_preference(actor_id: str, req: PreferenceCreate, request: Request):
        _check_actor(actor_id)
        authorize(request, role="analyst", actor_id=actor_id)
        try:
            deps.preference_store().remember_preference(actor_id, req.content)
        except memory_policy.PolicyViolation as e:
            raise ApiError(422, "policy_violation", str(e)) from None
        return PreferenceList(items=[PreferenceItem(id=i, content=c) for i, c in deps.preference_store().list_preferences(actor_id)])

    @app.delete("/v1/actors/{actor_id}/preferences/{record_id}", status_code=204)
    def delete_preference(actor_id: str, record_id: str, request: Request):
        _check_actor(actor_id)
        authorize(request, role="analyst", actor_id=actor_id)
        deps.preference_store().delete_preference(actor_id, record_id)
        return Response(status_code=204)

    # --- 運用の観測（第17章）: operator だけ ---

    @app.get("/metrics")
    def metrics(request: Request):
        """Prometheus の形式の指標。認証があるときは、operator のトークン（Bearer）が要る。"""
        authorize(request, role="operator")
        pending = _review_pending()
        if pending is not None:
            deps.observer.review_pending.set(pending)
        return Response(deps.observer.render(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/ops/status", response_model=OpsStatus, responses={s: {"model": ErrorResponse} for s in (401, 403)})
    def ops_status(request: Request):
        """直近の窓の指標と、基準を超えているもの。"""
        authorize(request, role="operator")
        window = alert_window()
        return OpsStatus(window_seconds=deps.alert_window_seconds, runs_in_window=len(window.runs),
                         measures=alerts.measure(window), alerts=[a.to_dict() for a in alerts.evaluate(window, deps.alert_rules)])

    @app.get("/readyz")
    def readyz():
        """受け付けてよい状態か。依存先（DB）に届くかを確かめる。届かなければ 503（詳細は返さない）。
        /healthz（プロセスが生きているか）と分ける: DB の障害で、コンテナを再起動しても直らない。再起動させず、受付だけを止める。"""
        try:
            (deps.readiness_check or _default_readiness)()
        except Exception:
            logger.error("not ready")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.get("/healthz")
    async def healthz():
        """生存の確認。LLM・DB には触れない（外部の障害で、プロセスが落ちたと誤判定しないため）。"""
        return {"status": "ok", "running": app.state.running}

    return app


def _default_readiness() -> None:
    from shared.postgres import database_url, get_database
    if database_url():
        with get_database().connection() as conn:
            conn.execute("SELECT 1")


def _check_actor(actor_id: str) -> None:
    try:
        memory_policy.check_actor_id(actor_id)
    except ValueError as e:
        raise ApiError(422, "invalid_request", str(e)) from None
