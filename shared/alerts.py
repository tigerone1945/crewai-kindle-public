"""Alert（第17章）: 直近の窓の集計が、基準を超えたら知らせる。

設計の方針:
- 症状で知らせる。「Tool が1回失敗した」ではなく、「回答できなかった実行が、窓の中で、基準を超えた」。1件の失敗は、再試行や Agent の立て直しで吸収される。
- 件数が少ないうちは、判定しない（min_samples）。2件中1件の失敗は、50% だが、情報ではない。
- 層で分ける。Runtime Health（動いているか）/ Agent Quality（Agent の振る舞いが悪くなっていないか）/ Business Quality（業務として困ることが起きていないか）。
  知らせる相手と、最初に見る場所が違う。
- 知らせるのは、状態が変わったときだけ（AlertTracker）。基準を超えている間、毎回通知しない。

基準の値は、既定の例である。自分のサービスで、平常時の値を測ってから決める（本文の「基準の決め方」）。
通知の送り先（メール・チャット）は、ここでは実装しない。標準出力へ、`alert_firing` / `alert_resolved` を1行で出す。ログの収集の仕組みから、送り先へつなぐ。
"""
import json
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from shared.observability import VALIDATION_LAYER, RunRecord, configure_stdout_logger

alert_log = logging.getLogger("alerts")

RUNTIME, AGENT, BUSINESS = "runtime_health", "agent_quality", "business_quality"
DEFAULT_WINDOW_SECONDS = 900  # 直近15分


def percentile(values: Sequence[float], q: float) -> float:
    """最も近い順位（nearest-rank）。補間しない（件数が少ないとき、実在しない値を作らない）。"""
    ordered = sorted(values)
    return ordered[max(math.ceil(q / 100 * len(ordered)), 1) - 1]


@dataclass(frozen=True)
class Window:
    runs: Sequence[RunRecord]
    requests: Sequence[tuple[float, str, int]]  # (時刻, route, status)
    review_pending: int | None = None

    @property
    def worked(self) -> list[RunRecord]:
        """分析のステップまで進んだ実行。入力の却下・承認待ちへの回しは、含めない（LLM を使っていない）。"""
        return [r for r in self.runs if r.outcome in ("analysis", "escalated", "error")]


# 指標: Window → (値, 母数)。母数が少なければ、判定しない
Metric = Callable[[Window], tuple[float, int] | None]


def _rate(numerator: int, population: Sequence | int) -> tuple[float, int] | None:
    n = population if isinstance(population, int) else len(population)
    return (numerator / n, n) if n else None


def _system_failure_rate(w: Window):
    worked = w.worked
    return _rate(sum(r.failed and r.failure_layer != VALIDATION_LAYER for r in worked), worked)


def _p95_duration(w: Window):
    worked = w.worked
    return (percentile([r.duration_ms / 1000 for r in worked], 95), len(worked)) if worked else None


def _busy_rate(w: Window):
    analyses = [s for _, route, s in w.requests if route == "/v1/analyses"]
    return _rate(sum(s == 429 for s in analyses), analyses)


def _first_pass_rate(w: Window):
    worked = w.worked
    return _rate(sum(r.first_pass for r in worked), worked)


def _tool_error_run_rate(w: Window):
    worked = w.worked
    return _rate(sum(r.tool_errors > 0 for r in worked), worked)


def _mean_tokens(w: Window):
    answered = [r for r in w.runs if r.answered]
    return (sum(r.total_tokens for r in answered) / len(answered), len(answered)) if answered else None


def _escalation_rate(w: Window):
    return _rate(sum(r.outcome == "escalated" for r in w.runs), w.runs)


def _rejection_rate(w: Window):
    return _rate(sum(r.outcome == "rejected" for r in w.runs), w.runs)


def _review_pending(w: Window):
    return (float(w.review_pending), 1) if w.review_pending is not None else None  # 件数そのもの。母数の概念はない


METRICS: dict[str, Metric] = {
    "system_failure_rate": _system_failure_rate, "p95_duration_seconds": _p95_duration, "busy_rate": _busy_rate,
    "first_pass_rate": _first_pass_rate, "tool_error_run_rate": _tool_error_run_rate, "mean_tokens": _mean_tokens,
    "escalation_rate": _escalation_rate, "rejection_rate": _rejection_rate, "review_pending": _review_pending,
}


@dataclass(frozen=True)
class AlertRule:
    name: str
    layer: str
    metric: str
    comparison: str  # ">" か "<"
    threshold: float
    min_samples: int
    description: str

    def __post_init__(self) -> None:
        if self.metric not in METRICS or self.comparison not in (">", "<"):
            raise ValueError(f"不正な規則: {self.name}")

    def breached(self, value: float) -> bool:
        return value > self.threshold if self.comparison == ">" else value < self.threshold


DEFAULT_RULES = (
    AlertRule("system_failure_rate_high", RUNTIME, "system_failure_rate", ">", 0.2, 10,
              "分析のステップまで進んだ実行の 20% 超が、システムの失敗（LLM・Tool・実行基盤）で、回答できなかった"),
    AlertRule("latency_p95_high", RUNTIME, "p95_duration_seconds", ">", 60.0, 10,
              "実行の所要時間の p95 が 60 秒を超えた（API の時間切れは 150 秒）"),
    AlertRule("busy_rate_high", RUNTIME, "busy_rate", ">", 0.1, 10,
              "分析の受付の 10% 超が、同時実行の上限で断られた（処理能力が足りない）"),
    AlertRule("first_pass_rate_low", AGENT, "first_pass_rate", "<", 0.8, 10,
              "1回で出力の検査を通った実行が 80% を下回った（プロンプト・モデル・Tool の説明の劣化の兆し）"),
    AlertRule("tool_error_runs_high", AGENT, "tool_error_run_rate", ">", 0.1, 10,
              "Tool が失敗した実行（Agent が立て直したものを含む）が 10% 超"),
    AlertRule("tokens_per_run_high", AGENT, "mean_tokens", ">", 6000.0, 10,
              "回答した実行の、平均 Token が 6,000 超（コストの増加。Process や Role の変更で起きる）"),
    AlertRule("escalation_rate_high", BUSINESS, "escalation_rate", ">", 0.1, 10,
              "回答を作れず、人へ引き継いだ実行が 10% 超（利用者が、答えを得られていない）"),
    AlertRule("rejection_rate_high", BUSINESS, "rejection_rate", ">", 0.3, 10,
              "入力を却下した実行が 30% 超（使い方が伝わっていない、または、探られている）"),
    AlertRule("review_backlog_high", BUSINESS, "review_pending", ">", 20.0, 1,
              "承認待ちが 20 件を超えた（承認者が足りない、または通知が届いていない）"),
)


@dataclass(frozen=True)
class Alert:
    rule: str
    layer: str
    value: float
    threshold: float
    samples: int
    description: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "layer": self.layer, "value": round(self.value, 3), "threshold": self.threshold,
                "samples": self.samples, "description": self.description}


def evaluate(window: Window, rules: Sequence[AlertRule] = DEFAULT_RULES) -> list[Alert]:
    alerts = []
    for rule in rules:
        measured = METRICS[rule.metric](window)
        if measured is None:
            continue
        value, samples = measured
        if samples >= rule.min_samples and rule.breached(value):
            alerts.append(Alert(rule.name, rule.layer, value, rule.threshold, samples, rule.description))
    return alerts


def measure(window: Window) -> dict[str, dict]:
    """すべての指標の、いまの値と母数。基準を超えていなくても、状況を見るために返す。"""
    out = {}
    for name, metric in METRICS.items():
        measured = metric(window)
        out[name] = {"value": round(measured[0], 3), "samples": measured[1]} if measured else {"value": None, "samples": 0}
    return out


class AlertTracker:
    """基準を超えた状態が続く間、毎回は知らせない。始まった（firing）と、終わった（resolved）ときだけ、1行出す。"""

    def __init__(self) -> None:
        self._firing: dict[str, Alert] = {}

    @property
    def firing(self) -> list[Alert]:
        return list(self._firing.values())

    def update(self, alerts: Sequence[Alert], *, now: float | None = None) -> tuple[list[Alert], list[str]]:
        """(新しく firing になったもの, resolved になった規則の名前) を返し、ログへ出す。"""
        current = {a.rule: a for a in alerts}
        started = [a for name, a in current.items() if name not in self._firing]
        resolved = [name for name in self._firing if name not in current]
        self._firing = current
        ts = time.time() if now is None else now
        for a in started:
            alert_log.warning(json.dumps({"event": "alert_firing", "ts": ts, **a.to_dict()}, ensure_ascii=False))
        for name in resolved:
            alert_log.info(json.dumps({"event": "alert_resolved", "ts": ts, "rule": name}, ensure_ascii=False))
        return started, resolved


def configure_alert_logging() -> None:
    configure_stdout_logger(alert_log)
