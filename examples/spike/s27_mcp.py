"""Spike 27: 付録K の実測。CrewAI 1.15.23 の MCP 連携（Agent の mcps）を、ローカルの stdio サーバーで確かめる。
A. Tool の発見: 接続して、どんな Tool が、どんな名前で見えるか。フィルターで、見せる範囲を絞れるか
B. 実行: 読み取りの Tool を使った分析が、Trace に残るか（Tool 名・Role）。ローカルの Tool との所要時間の差
C. 副作用: 登録の Tool を Agent に見せると、人の承認なしに、実行されてしまうか
D. サーバー側の検査: 不正な入力（形式・実在・軸）を、サーバーが拒むか
使い方: s27_mcp.py [A|B|C|D|ABCD] [回数]
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai import Agent, Crew, Process, Task
from crewai.mcp import MCPServerStdio
from crewai.mcp.filters import create_static_tool_filter

from shared.guardrails import OutputGuard
from shared.review_requests import ReviewStore
from shared.sales_data import get_llm
from shared.sales_db import SalesRepository
from shared.schemas import SalesAnalysisResult
from shared.tracing import trace_run

SERVER = str(Path(__file__).resolve().parents[1] / "mcp" / "sales_mcp_server.py")


def server(tmp: str, allowed=None) -> MCPServerStdio:
    return MCPServerStdio(command=sys.executable, args=[SERVER], env={**os.environ, "REVIEW_DB": f"{tmp}/review.db"},
                          tool_filter=create_static_tool_filter(allowed_tool_names=allowed) if allowed else None)


def agent_with(mcp: MCPServerStdio) -> Agent:
    return Agent(role="売上分析アシスタント", goal="Toolの結果だけを根拠に答える", backstory="数値は自分で計算せず、Toolの結果を使う。", mcps=[mcp], llm=get_llm())


def run(agent: Agent, question: str, structured: bool = True):
    task = Task(name="analysis", description=f"質問: {question}", expected_output="分析結果" if structured else "実行した結果の報告", agent=agent,
                **({"output_pydantic": SalesAnalysisResult} if structured else {}))
    with trace_run() as trace:
        out = Crew(agents=[agent], tasks=[task], process=Process.sequential).kickoff()
    return out, trace


def part_a() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for label, allowed in (("フィルターなし", None), ("読み取りだけ", ["sales_by_axis"])):
            tools = agent_with(server(tmp, allowed)).get_mcp_tools(agent_with(server(tmp, allowed)).mcps)
            print(f"A. {label}: {[t.name for t in tools]}")
            if tools:
                print("   説明の例:", tools[0].description[:90].replace("\n", " "))


def part_b(runs: int) -> None:
    from shared.sales_tools import SALES_TOOLS
    with tempfile.TemporaryDirectory() as tmp:
        q = "地域別の売上を比較してください。"
        mcp_rows, local_rows = [], []
        for _ in range(runs):
            a = agent_with(server(tmp, ["sales_by_axis"]))
            t0 = time.time()
            out, trace = run(a, q)
            s = trace.summary()
            guard = OutputGuard(SalesRepository.default())
            ok = out.pydantic is not None and not guard._inspect(type("O", (), {"pydantic": out.pydantic, "raw": out.raw})())[1]
            mcp_rows.append((time.time() - t0, s["tool_calls"], s["tool_roles"], ok))
            local = Agent(role="売上分析アシスタント", goal="Toolの結果だけを根拠に答える", backstory="数値は自分で計算せず、Toolの結果を使う。", tools=SALES_TOOLS, llm=get_llm())
            t0 = time.time()
            out2, trace2 = run(local, q)
            local_rows.append((time.time() - t0, trace2.summary()["tool_calls"]))
        print(f"B. MCP 経由: 秒 {[round(r[0], 1) for r in mcp_rows]} 中央値 {statistics.median(r[0] for r in mcp_rows):.1f} / Tool {mcp_rows[0][1]} / Role と Tool {mcp_rows[0][2]} / 検査を通った {sum(r[3] for r in mcp_rows)}/{runs}")
        print(f"   ローカル: 秒 {[round(r[0], 1) for r in local_rows]} 中央値 {statistics.median(r[0] for r in local_rows):.1f} / Tool {local_rows[0][1]}")


def part_c(runs: int) -> None:
    for i in range(runs):
        with tempfile.TemporaryDirectory() as tmp:
            a = agent_with(server(tmp))  # フィルターなし: 登録の Tool も見える
            out, trace = run(a, "O0001をレビュー対象として登録してください。依頼者は user-001 です。", structured=False)
            registered = ReviewStore(f"{tmp}/review.db").list_requests()
            print(f"C. {i + 1}: Tool {trace.summary()['tool_calls']} / 登録された {[r['order_id'] for r in registered]}（承認者の記録: {[r['approved_by'] for r in registered]}）")


def part_d() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        a = agent_with(server(tmp))
        found = a.get_mcp_tools(a.mcps)
        tools = {n: next(t for t in found if t.name.endswith("_" + n)) for n in ("create_review_request", "sales_by_axis")}  # 名前は、サーバーの識別子が前に付く（A）
        for name, args in (("create_review_request", {"order_id": "X1; DROP TABLE", "requested_by": "user-001"}),
                           ("create_review_request", {"order_id": "O9999", "requested_by": "user-001"}),
                           ("sales_by_axis", {"axis": "customer_phone"}),
                           ("create_review_request", {"order_id": "O0001", "requested_by": "user-001"}),
                           ("create_review_request", {"order_id": "O0001", "requested_by": "user-001"})):
            try:
                print(f"D. {name}({args}) → {str(tools[name].run(**args))[:90]}")
            except Exception as e:
                print(f"D. {name}({args}) → 例外 {type(e).__name__}: {str(e)[:90]}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "ABCD"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if "A" in which:
        part_a()
    if "D" in which:
        part_d()
    if "B" in which:
        part_b(n)
    if "C" in which:
        part_c(n)
