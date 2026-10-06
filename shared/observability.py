"""運用の観測（第17章）: 1回の実行の記録（Trace。第6章）を、運用で見られる形にする。

Trace は「1回の実行を、あとから1件ずつ辿る」ためのもの。運用では、その前に「いま、全体として、おかしくないか」を知る必要がある。

  Log     実行が終わるたびに、安全な要約を1行（JSON）で出す。run_id で、Trace・アクセスの記録・エラー応答と結びつく
  Metric  実行・失敗・所要時間・Token を、数える（Prometheus の形式）。ラベルは、決まった値だけ（run_id・利用者・質問を入れない）
  Alert   直近の窓の集計が、基準を超えたら知らせる（shared/alerts.py）

観測するものは、3つの層に分ける。何が壊れたか（Runtime Health）と、何が悪くなったか（Agent Quality）と、
業務として困ることが起きたか（Business Quality）は、別の問いであり、直す人も違う。

失敗の層（failure_layer）は、Trace から決める。失敗は、下の層で起きて、上の層へ伝わる。
  Tool → Agent → Task → Crew → Flow
そのため、最初に error になった Event の層が、根本の原因の層（同時刻なら、より下の層）。上の層の error は、結果にすぎない。

BR-10: 記録するのは、種類・名前・件数・時間・Token・理由コードだけ。質問・回答・Tool の引数や出力の中身は、記録しない。
"""
import dataclasses
import json
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

# 下の層ほど小さい。同じ時刻に error が並んだら、小さい方（根本）を原因とする
_LAYER_ORDER = {"tool": 0, "llm": 1, "agent": 2, "task": 3, "crew": 4, "flow": 5}
INPUT_LAYER = "input"  # 入力の検査で止めた（LLM を呼んでいない）。利用者の入力の問題
VALIDATION_LAYER = "output_validation"  # 出力の検査（Guardrail）を使い切った。LLM が、正しい回答を作れなかった
SERVICE_LAYER = "service"  # 実行の外側（同時実行の上限・時間切れ・想定外の例外）
FAILURE_LAYERS = (INPUT_LAYER, VALIDATION_LAYER, *_LAYER_ORDER, SERVICE_LAYER)

OUTCOMES = ("analysis", "review_pending", "rejected", "review_invalid", "escalated", "error")
ROUTES = ("single", "integrated", "review_request", "rejected", "unknown")
DURATION_BUCKETS = (1, 2, 5, 10, 20, 40, 80, 150)  # 秒。API のタイムアウト（150秒）まで
OTHER = "other"

run_log = logging.getLogger("runs")  # 実行ごとの記録。アクセスの記録（audit）・業務上の承認の監査・Trace とは別


class _StdoutHandler(logging.StreamHandler):
    """出力先は、書くときの sys.stdout（コンテナの標準出力。テストでの差し替えにも従う）。"""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_stdout_logger(logger: logging.Logger) -> None:
    """記録を、標準出力へ1行1件で出す。設定しないと INFO は、どこにも出ない（第16章のアクセスの記録と同じ落とし穴）。"""
    if not any(isinstance(h, _StdoutHandler) for h in logger.handlers):
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 親（root）へも流すと、二重に出る


def configure_run_logging() -> None:
    configure_stdout_logger(run_log)


@dataclass(frozen=True)
class RunRecord:
    """1回の実行の、安全な要約。質問・回答・Tool の中身を持たない（持てない）。"""

    run_id: str
    ts: float  # 実行が終わった時刻（epoch 秒）
    route: str
    outcome: str  # OUTCOMES のいずれか
    status: str  # ok / error / rejected（Trace の判定）
    duration_ms: int
    attempts: int = 0
    llm_calls: int = 0
    tool_calls: tuple[str, ...] = ()
    tool_errors: int = 0  # Agent が立て直した Tool の失敗も数える（最終結果が成功でも、質の劣化の兆し）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reason: str = ""  # rejected: 入力検査の理由コード
    error_kind: str = ""  # escalated: guardrail / transient
    failure_layer: str = ""  # 失敗のとき、根本の層。成功なら空

    @property
    def failed(self) -> bool:
        return bool(self.failure_layer) and self.failure_layer != INPUT_LAYER  # 入力の却下は、システムの失敗ではない

    @property
    def answered(self) -> bool:
        return self.outcome == "analysis"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def first_pass(self) -> bool:
        return self.answered and self.attempts == 1

    @classmethod
    def from_log(cls, d: dict) -> "RunRecord":
        """to_log の逆。未知の項目は無視する（ログの形が増えても、古い集計が読める）。"""
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{**{k: v for k, v in d.items() if k in names}, "tool_calls": tuple(d.get("tool_calls", ()))})

    def to_log(self) -> dict:
        d = asdict(self)
        d["event"] = "run_completed"
        d["tool_calls"] = list(self.tool_calls)
        return d


def classify_failure(outcome: str, error_kind: str, events: Sequence) -> str:
    """失敗の根本の層。成功（回答した・承認待ち）なら空。events は、Trace の TraceEvent。"""
    if outcome in ("analysis", "review_pending"):
        return ""
    if outcome in ("rejected", "review_invalid"):
        return INPUT_LAYER
    if outcome == "escalated" and error_kind == "guardrail":
        return VALIDATION_LAYER
    errors = [e for e in events if e.status == "error" and e.kind in _LAYER_ORDER]
    if errors:
        first = min(errors, key=lambda e: (e.at_ms, _LAYER_ORDER[e.kind]))
        return first.kind
    return SERVICE_LAYER  # Trace に、原因になった Event がない（想定外の例外、または Trace が取れなかった）


def build_run_record(run_id: str, outcome, trace, *, now: float | None = None, known_tools: Iterable[str] | None = None) -> RunRecord:
    """Flow の結果（FlowOutcome。例外なら None）と Trace から、記録を作る。"""
    summary = trace.summary()
    events = trace.events
    kind = outcome.kind if outcome is not None else "error"
    error_kind = getattr(outcome, "error_kind", "") if outcome is not None else ""
    tools = tuple(summary["tool_calls"])
    return RunRecord(
        run_id=run_id, ts=time.time() if now is None else now,
        route=getattr(outcome, "route", "") or "unknown", outcome=kind if kind in OUTCOMES else "error",
        status=summary["status"], duration_ms=summary["duration_ms"] or 0,
        attempts=getattr(outcome, "attempts", 0) or summary["attempts"], llm_calls=summary["llm_calls"],
        tool_calls=tools, tool_errors=sum(e.kind == "tool" and e.status == "error" for e in events),
        prompt_tokens=summary["prompt_tokens"], completion_tokens=summary["completion_tokens"],
        reason=getattr(outcome, "reason", "") if outcome is not None else "", error_kind=error_kind,
        failure_layer=classify_failure(kind, error_kind, events))


def _bounded(value: str, allowed: Iterable[str]) -> str:
    """ラベルの値は、決まった集合の中だけ。外のものは other にまとめる（ラベルの組み合わせの爆発を防ぐ）。"""
    return value if value in allowed else OTHER


def default_known_tools() -> frozenset[str]:
    from shared.roles import QUALITY_TOOLS
    from shared.sales_tools import SALES_TOOLS
    return frozenset(t.name for t in (*SALES_TOOLS, *QUALITY_TOOLS))


class Observer:
    """実行の記録を受け取り、Log・Metric・直近の窓（Alert の入力）へ振り分ける。1つのサービスに1つ。

    Metric は、プロセスの中のメモリにある。再起動で0に戻る（Prometheus は、カウンターの減少をリセットとして扱う）。
    複数のワーカーに分けると、ワーカーごとの値になる（prometheus_client の multiprocess モードが必要）。
    """

    def __init__(self, *, window_size: int = 1000, known_tools: Iterable[str] | None = None) -> None:
        self.registry = CollectorRegistry()
        self._known_tools = frozenset(known_tools) if known_tools is not None else None
        self._lock = threading.Lock()
        self._runs: deque[RunRecord] = deque(maxlen=window_size)
        self._requests: deque[tuple[float, str, int]] = deque(maxlen=window_size)  # (時刻, route, status)
        r = self.registry
        self.analyses = Counter("analyses_total", "Completed analysis runs", ["route", "outcome"], registry=r)
        self.failures = Counter("analysis_failures_total", "Failed runs by root layer", ["layer"], registry=r)
        self.duration = Histogram("analysis_duration_seconds", "Run duration including retries", ["route"],
                                  buckets=DURATION_BUCKETS, registry=r)
        self.tokens = Counter("llm_tokens_total", "LLM tokens", ["type"], registry=r)
        self.llm_calls = Counter("llm_calls_total", "LLM calls", registry=r)
        self.tool_calls = Counter("tool_calls_total", "Tool calls", ["tool"], registry=r)
        self.tool_errors = Counter("tool_errors_total", "Tool errors, including ones the agent recovered from", registry=r)
        self.retries = Counter("analysis_retries_total", "Extra attempts after the first", registry=r)
        self.in_flight = Gauge("analyses_in_flight", "Analyses running now", registry=r)
        self.http_requests = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"], registry=r)
        self.http_duration = Histogram("http_request_duration_seconds", "HTTP request duration", ["route"],
                                       buckets=DURATION_BUCKETS, registry=r)
        self.review_pending = Gauge("reviews_pending", "Review requests waiting for a decision", registry=r)

    @property
    def known_tools(self) -> frozenset[str]:
        if self._known_tools is None:
            self._known_tools = default_known_tools()
        return self._known_tools

    def record_run(self, record: RunRecord) -> None:
        route = _bounded(record.route, ROUTES)
        self.analyses.labels(route, record.outcome).inc()
        if record.failure_layer:
            self.failures.labels(_bounded(record.failure_layer, FAILURE_LAYERS)).inc()
        self.duration.labels(route).observe(record.duration_ms / 1000)
        self.tokens.labels("prompt").inc(record.prompt_tokens)
        self.tokens.labels("completion").inc(record.completion_tokens)
        self.llm_calls.inc(record.llm_calls)
        for name in record.tool_calls:
            self.tool_calls.labels(_bounded(name, self.known_tools)).inc()
        self.tool_errors.inc(record.tool_errors)
        self.retries.inc(max(record.attempts - 1, 0))
        with self._lock:
            self._runs.append(record)
        run_log.info(json.dumps(record.to_log(), ensure_ascii=False))

    def record_request(self, method: str, route: str, status: int, seconds: float, *, now: float | None = None) -> None:
        self.http_requests.labels(method, route, str(status)).inc()
        self.http_duration.labels(route).observe(seconds)
        with self._lock:
            self._requests.append((time.time() if now is None else now, route, status))

    def runs_since(self, since: float) -> list[RunRecord]:
        with self._lock:
            return [r for r in self._runs if r.ts >= since]

    def requests_since(self, since: float) -> list[tuple[float, str, int]]:
        with self._lock:
            return [r for r in self._requests if r[0] >= since]

    def render(self) -> bytes:
        return generate_latest(self.registry)
