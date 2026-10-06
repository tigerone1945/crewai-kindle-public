"""API の契約（第14章）: Request / Response / Error の型。サービス（shared/api.py）と、利用側（shared/api_client.py、UI）が共有する。

CrewAI に依存しない（利用側が CrewAI を import せずに済むように、pydantic と shared/schemas.py だけを使う）。

契約の方針:
- 入力は、サービスが受け付ける形を厳しく決める（未知の項目は拒否、長さの上限）。意味の検査（範囲外・秘密情報など）は、サービスの中の入力検査（第7章）が行う。
- 出力は、利用者に見せてよい項目だけ。内部のエラー文・スタックトレース・Trace の中身は含めない。
- 失敗は、HTTP のステータスと、同じ形のエラー本体（ErrorResponse）で返す。呼び出し側は code で分岐する。
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from shared.schemas import SalesAnalysisResult

ActorId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
FlowId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{8,64}$")]

ErrorCode = Literal[
    "invalid_request",     # 422: 契約に合わない（型・長さ・未知の項目）
    "input_rejected",      # 422: 入力の検査で停止（範囲外・秘密情報・個人情報・指示の上書き）
    "unauthenticated",     # 401: 認証できない（トークンがない・不正・期限切れ）。理由は区別して返さない
    "forbidden",           # 403: 認証できたが、この操作は許されない（役割がない・他人の名前での操作）
    "review_invalid",      # 422: レビュー依頼にできない（複数の注文・対象外の注文）
    "policy_violation",    # 422: Memory Policy に反する（好みに数値や秘密情報を含めたなど）
    "self_approval",       # 403: 依頼した本人は承認できない
    "already_resolved",    # 409: 承認待ちが、すでに処理済み、または存在しない
    "busy",                # 429: 同時に処理できる数の上限。しばらくして再試行
    "analysis_failed",     # 502: 出力の検査を使い切り、正しい回答を作れなかった
    "unavailable",         # 503: 一時的な失敗の再試行を使い切った
    "timeout",             # 504: 処理が時間内に終わらなかった
    "internal",            # 500: 想定外。詳細は返さず、run_id で調査する
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoryTurn(_Strict):
    """会話の続きを解釈するための、直前の質問と要約。数値の正本にはしない（第5章）。"""

    question: str = Field(max_length=500)
    analysis_type: str = Field(max_length=40)
    summary: str = Field(max_length=1000)


class AnalysisRequest(_Strict):
    question: str = Field(min_length=1, max_length=2000)
    actor_id: ActorId  # 認証・認可は第16章。ここでは、利用者を区別するための識別子
    history: list[HistoryTurn] = Field(default_factory=list, max_length=5)


class AnalysisResponse(BaseModel):
    run_id: str  # 調査のための識別子。Trace・ログと突き合わせる
    kind: Literal["analysis", "review_pending"]
    route: Literal["single", "integrated", "review_request"]
    attempts: int = 0
    message: str = ""
    result: SalesAnalysisResult | None = None  # kind == "analysis"
    review_flow_id: str | None = None  # kind == "review_pending"。承認・却下で使う


class ErrorField(BaseModel):
    field: str
    problem: str  # 入力の値は含めない（秘密情報を、エラーで反射しない）


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str  # 利用者へ見せてよい文
    run_id: str | None = None
    retry_after_seconds: int | None = None
    fields: list[ErrorField] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


class PendingReview(BaseModel):
    flow_id: str
    order_id: str
    requested_by: str
    order: dict | None = None  # 対象の注文の事実（日付・種別・カテゴリ・状態・売上）。氏名は含まない
    problem: str | None = None  # 注文の状態が変わり、対象にできなくなっている場合の理由


class PendingReviewList(BaseModel):
    items: list[PendingReview]


class DecisionRequest(_Strict):
    decision: Literal["approve", "reject"]
    approver_id: ActorId


class DecisionResponse(BaseModel):
    result: Literal["registered", "cancelled", "duplicate", "not_eligible"]


class PreferenceCreate(_Strict):
    content: str = Field(min_length=1, max_length=200)


class PreferenceItem(BaseModel):
    id: str
    content: str


class PreferenceList(BaseModel):
    items: list[PreferenceItem]


class MeResponse(BaseModel):
    """呼び出している本人。認証が無効（開発用）なら auth_required が false で、他は空。"""

    auth_required: bool
    actor_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    credential_id: str | None = None


class OpsStatus(BaseModel):
    """運用の状況（operator 向け）。直近の窓の指標と、基準を超えているもの。質問・回答・利用者の情報は含まない。"""

    window_seconds: int
    runs_in_window: int
    measures: dict[str, dict]  # 指標名 → {"value", "samples"}。母数が足りない指標は value が None のこともある
    alerts: list[dict]  # 基準を超えているもの（規則・層・値・基準・母数・説明）
