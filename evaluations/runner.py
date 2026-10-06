"""Evaluation の実行（第18章）。Case を、本番と同じ入口（Flow の run_question）から実行し、観測（Observation）にして採点する。

Case の種類は、expected のキーと kind で決まる。
  分析の Case      質問を（複数ターンなら会話として）流し、Tool・Role・Token を Trace から集める
  拒否の Case      入力の検査で止まること（LLM・Tool が0回）
  承認の Case      承認の前に登録されず、承認・却下のそれぞれで、期待どおりに登録される（シナリオごとに実行）
  Memory の Case   好みを入れる・入れない（対照）、事実の保存を拒む
  flow_branch      質問ごとの経路（LLM を呼ばない）
  human_gate       自己承認・二重の決定を止める（LLM を呼ばない）
LLM を呼ばない Case は、決定的（deterministic）で、1回だけ実行する。LLM を呼ぶ Case は、確率的で、複数回実行して、合格率で見る。
"""
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import shared.analysis_flow as analysis_flow
import shared.review_flow as review_flow
from evaluations.grading import Grade, Observation, grade
from shared.analysis_flow import choose_route
from shared.conversation import Turn
from shared.memory_policy import PolicyViolation, PreferenceStore
from shared.review_requests import ReviewStore
from shared.sales_db import SalesRepository
from shared.tracing import trace_run

EVAL_DIR = Path(__file__).resolve().parent
CASE_FILES = ("evaluation_cases.json", "crewai_specific_cases.json", "failure_cases.json")
ACTOR, APPROVER = "user-001", "approver-001"


def load_cases(files=CASE_FILES, directory: Path = EVAL_DIR) -> list[dict]:
    cases = []
    for name in files:
        path = directory / name
        if path.exists():
            for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
                cases.append({**case, "_file": name})
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Case の id が重複しています: {sorted({i for i in ids if ids.count(i) > 1})}")
    return cases


def case_kind(case: dict) -> str:
    if case.get("kind"):
        return case["kind"]
    expected = case.get("expected", {})
    if expected.get("must_be_refused"):
        return "refusal"
    if "requires_approval_before" in expected:
        return "approval"
    if "setup" in case or "memory_records_must_not_match" in expected:
        return "memory"
    return "analysis"


def is_deterministic(case: dict) -> bool:
    """LLM を呼ばない Case。結果が毎回同じなので、1回だけ実行する。"""
    return case_kind(case) in ("refusal", "approval", "flow_branch", "human_gate")


def scenarios_of(case: dict) -> list[str]:
    if case_kind(case) == "approval":
        return [s["id"] for s in case["scenarios"]]
    if "control" in case:
        return ["with_memory", "control"]
    return [""]


@dataclass
class Harness:
    """実行の入口と、外の依存を束ねる。テストでは、偽物に差し替える。"""

    run_question: Callable = analysis_flow.run_question
    resolve_review: Callable = review_flow.resolve_review
    memory_factory: Callable[[Path], object] | None = None  # 保存先を受け取り、Memory を返す。None なら、本物の Memory

    def memory(self, path: Path):
        if self.memory_factory:
            return self.memory_factory(path)
        from crewai.memory.unified_memory import Memory

        from shared.sales_data import MODEL
        return Memory(llm=MODEL, storage=str(path))

    # --- 1回の実行 ---

    def observe(self, case: dict, scenario: str = "") -> Observation:
        obs = Observation(case["id"], scenario)
        kind = case_kind(case)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            store, flow_db = ReviewStore(tmp / "review.db"), tmp / "flows.db"
            if kind == "flow_branch":
                self._flow_branch(case, obs)
            elif kind == "human_gate":
                self._human_gate(obs, store, flow_db)
            elif kind == "approval":
                self._approval(case, scenario, obs, store, flow_db)
            elif kind == "memory":
                self._memory(case, scenario, obs, store, flow_db, tmp)
            else:
                self._turns(case["turns"], obs, store, flow_db)
        return obs

    def _turn(self, question: str, obs: Observation, store, flow_db, history=(), preferences_for=None):
        with trace_run() as trace:
            outcome = self.run_question(question, ACTOR, review_store=store, flow_db=flow_db, history=history, preferences_for=preferences_for)
        s = trace.summary()
        obs.results.append(outcome.result)
        obs.outcome_kinds.append(outcome.kind)
        obs.routes.append(outcome.route)
        obs.tools_called += s["tool_calls"]
        obs.tool_roles += [tuple(x) for x in s["tool_roles"]]
        obs.role_names += s["roles"]
        obs.llm_calls += s["llm_calls"]
        obs.tokens += s["prompt_tokens"] + s["completion_tokens"]
        obs.seconds += (s["duration_ms"] or 0) / 1000
        obs.attempts += outcome.attempts
        return outcome

    def _turns(self, turns: list[str], obs: Observation, store, flow_db, preferences_for=None) -> None:
        history: list[Turn] = []
        for question in turns:
            outcome = self._turn(question, obs, store, flow_db, tuple(history), preferences_for)
            if outcome.result is not None:
                history.append(Turn.from_result(question, outcome.result))

    def _approval(self, case: dict, scenario: str, obs: Observation, store, flow_db) -> None:
        outcome = self._turn(case["turns"][0], obs, store, flow_db)
        obs.extras["registrations_before_decision"] = len(store.list_requests())
        obs.extras["llm_calls"] = obs.llm_calls
        if outcome.kind == "review_pending":
            decision = next(s["human_decision"] for s in case["scenarios"] if s["id"] == scenario)
            self.resolve_review(outcome.review_flow_id, decision, APPROVER, store, flow_db)
        obs.extras["registrations_after_decision"] = store.list_requests()

    def _flow_branch(self, case: dict, obs: Observation) -> None:
        obs.extras["kind"] = "flow_branch"
        obs.extras["pairs"] = [(q, expected, choose_route(q)) for q, expected in case["route_pairs"]]
        obs.extras["known_limits"] = [(q, expected, choose_route(q)) for q, expected in case.get("known_limits", [])]

    def _human_gate(self, obs: Observation, store, flow_db) -> None:
        """承認の関門を、迂回できないこと。自己承認・二重の決定・却下のあとの登録。"""
        obs.extras["kind"] = "human_gate"
        failures = []
        ticket = review_flow.start_review("O0001", ACTOR, store, flow_db)
        try:
            self.resolve_review(ticket.flow_id, "approve", ACTOR, store, flow_db)  # 依頼した本人が、承認する
            failures.append("依頼者が、自分の依頼を承認できました")
        except review_flow.SelfApprovalError:
            pass
        if store.list_requests():
            failures.append("自己承認の試みで、登録されました")
        try:
            self.resolve_review(ticket.flow_id, "approve", APPROVER, store, flow_db)
        except ValueError:  # 自己承認が通ってしまい、承認待ちがすでに無い、などの場合。ここで落とさず、失敗として記録する
            failures.append("正当な承認者の承認が、受け付けられませんでした")
        try:
            self.resolve_review(ticket.flow_id, "reject", "approver-002", store, flow_db)  # すでに決まったものを、もう一度決める
            failures.append("すでに決まった依頼を、もう一度決められました")
        except ValueError:
            pass
        if len(store.list_requests()) != 1:
            failures.append(f"登録が {len(store.list_requests())} 件です（1件のはず）")
        ticket2 = review_flow.start_review("O0002", ACTOR, store, flow_db)
        self.resolve_review(ticket2.flow_id, "reject", APPROVER, store, flow_db)
        if any(r["order_id"] == "O0002" for r in store.list_requests()):
            failures.append("却下した依頼が、登録されました")
        obs.extras["failures"] = failures

    def _memory(self, case: dict, scenario: str, obs: Observation, store, flow_db, tmp: Path) -> None:
        memory = self.memory(tmp / "memory")
        prefs = PreferenceStore(memory)
        try:
            seed = case.get("setup", {}).get("memory_seed")
            if seed and scenario != "control":
                prefs.remember_preference(ACTOR, seed["content"])
            fact = case.get("setup", {}).get("attempt_fact")
            if fact:
                try:
                    prefs.remember_preference(ACTOR, fact)
                    obs.extras["fact_refused"] = False
                except PolicyViolation:
                    obs.extras["fact_refused"] = True
            self._turns(case["turns"][-1:], obs, store, flow_db, preferences_for=lambda q: prefs.recall_preferences(ACTOR, q))
            obs.extras["memory_records"] = [content for _, content in prefs.list_preferences(ACTOR)]
        finally:
            prefs.close()


# --- 採点と集計 ---

def evaluate_case(case: dict, harness: Harness, runs: int, repo=None) -> dict:
    """Case を runs 回（決定的なら1回）実行して採点し、軸ごとの合格数と、時間・Token を返す。"""
    repo = repo or SalesRepository.default()
    n = 1 if is_deterministic(case) else runs
    per_axis: dict[str, list[int]] = {}
    failures: list[str] = []
    observations: list[Observation] = []
    all_passed = 0  # 同じ実行の、すべての軸が合格した数（Case の合格）
    for scenario in scenarios_of(case):
        for _ in range(n):
            obs = harness.observe(case, scenario)
            observations.append(obs)
            grades = grade(case, obs, repo)
            all_passed += all(g.passed for g in grades)
            for g in grades:
                key = f"{g.axis}[{scenario}]" if scenario else g.axis
                passed, total = per_axis.setdefault(key, [0, 0])
                per_axis[key] = [passed + g.passed, total + 1]
                if not g.passed:
                    failures.append(f"{key}: {g.detail}")
    return {
        "id": case["id"], "kind": case_kind(case), "deterministic": is_deterministic(case), "n": len(observations), "passes": all_passed,
        "axes": per_axis, "failures": failures[:20],
        "mean_tokens": round(sum(o.tokens for o in observations) / len(observations)),
        "median_seconds": sorted(o.seconds for o in observations)[len(observations) // 2],
        "known_limits": _known_limits(observations),
    }


def _known_limits(observations: list[Observation]) -> list[dict]:
    """既知の限界（いまは誤る質問）の現状。直ったものは、昇格（Case への移動）の候補。"""
    out = []
    for o in observations:
        for q, expected, actual in o.extras.get("known_limits", []):
            out.append({"question": q, "expected": expected, "actual": actual, "resolved": expected == actual})
    return out
