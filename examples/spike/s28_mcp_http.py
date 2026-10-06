"""Spike 28: 付録K の、出版前の再確認。s27 で確かめていなかった部分。
A. Streamable HTTP + Bearer 認証: トークンなし・誤り・正しい場合に、発見できるか
B. 2つのサーバー（stdio + HTTP）を同時に: Tool の名前・衝突
C. 障害: 接続できないサーバー（HTTP が落ちている、stdio の起動に失敗）で、発見・実行がどうなるか
D. 接続できないサーバーがあるまま、分析を実行すると、Agent は何をするか（LLM を呼ぶ）
E. 動いている HTTP サーバーで、分析が完了するか（Tool の名前に : を含む）
使い方: s28_mcp_http.py [A|B|C|D|E|ABCDE]
"""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crewai import Agent
from crewai.mcp import MCPServerHTTP, MCPServerStdio

from shared.sales_data import get_llm

ROOT = Path(__file__).resolve().parents[2]
PORT = 18765


def names(mcps) -> list[str]:
    a = Agent(role="r", goal="g", backstory="b", mcps=mcps, llm=get_llm())
    return [t.name for t in a.get_mcp_tools(a.mcps)]


class HttpServer:
    def __enter__(self):
        import socket
        with socket.socket() as probe:  # 別のプロセスが、同じポートを使っていると、そちらに接続して、誤った結論になる（実際に起きた）
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError(f"ポート {PORT} は、すでに使われています")
        self.token = secrets.token_urlsafe(24)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "examples/mcp/sales_mcp_http_server.py"), str(PORT)],
                                     env={**os.environ, "MCP_TOKEN": self.token}, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                import socket
                socket.create_connection(("127.0.0.1", PORT), 0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(5)


def attempt(label, fn):
    t0 = time.time()
    try:
        out = fn()
        print(f"{label} → {out}  ({time.time() - t0:.1f}s)", flush=True)
    except Exception as e:
        print(f"{label} → 例外 {type(e).__name__}: {str(e)[:140]}  ({time.time() - t0:.1f}s)", flush=True)


URL = f"http://localhost:{PORT}/mcp"  # IP アドレスだと、Tool の名前が数字で始まり、LLM の API に拒否される（E で確認）
URL_IP = f"http://127.0.0.1:{PORT}/mcp"


def part_a():
    with HttpServer() as s:
        attempt("A. トークンなし", lambda: names([MCPServerHTTP(url=URL)]))
        attempt("A. 誤ったトークン", lambda: names([MCPServerHTTP(url=URL, headers={"Authorization": "Bearer wrong"})]))
        attempt("A. 正しいトークン", lambda: names([MCPServerHTTP(url=URL, headers={"Authorization": f"Bearer {s.token}"})]))


def part_b():
    with HttpServer() as s:
        http = MCPServerHTTP(url=URL, headers={"Authorization": f"Bearer {s.token}"})
        stdio = MCPServerStdio(command="python", args=["examples/mcp/sales_mcp_server.py"], env={**os.environ, "REVIEW_DB": "/tmp/s28.db"})
        attempt("B. stdio + HTTP", lambda: names([stdio, http]))


def part_c():
    attempt("C. HTTP が落ちている", lambda: names([MCPServerHTTP(url="http://127.0.0.1:1/mcp")]))
    attempt("C. stdio の起動に失敗（存在しないコマンド）", lambda: names([MCPServerStdio(command="no-such-command-xyz", args=[])]))
    attempt("C. stdio が、すぐ終了する", lambda: names([MCPServerStdio(command="python", args=["-c", "import sys; sys.exit(3)"])]))


def run_analysis_with(mcps, label):
    from crewai import Crew, Process, Task

    from shared.schemas import SalesAnalysisResult
    from shared.tracing import trace_run
    a = Agent(role="売上分析アシスタント", goal="Toolの結果だけを根拠に答える", backstory="数値は自分で計算せず、Toolの結果を使う。",
              mcps=mcps, llm=get_llm())
    task = Task(name="analysis", description="質問: 地域別の売上を比較してください。", expected_output="分析結果", agent=a, output_pydantic=SalesAnalysisResult)
    try:
        with trace_run() as trace:
            out = Crew(agents=[a], tasks=[task], process=Process.sequential).kickoff()
        s = trace.summary()
        print(f"{label} 実行は完了した。Tool {s['tool_calls']} / 回答の数値の例: {[ (m.label, m.value) for m in (out.pydantic.metrics if out.pydantic else [])][:3]}")
    except Exception as e:
        print(f"{label} 例外 {type(e).__name__}: {str(e)[:160]}")


def part_d():
    run_analysis_with([MCPServerHTTP(url="http://127.0.0.1:1/mcp")], "D. 接続できないサーバーで:")


def part_e():
    with HttpServer() as s:
        h = {"Authorization": f"Bearer {s.token}"}
        run_analysis_with([MCPServerHTTP(url=URL_IP, headers=h)], "E. URL が IP アドレス（Tool 名が数字で始まる）:")
        run_analysis_with([MCPServerHTTP(url=URL, headers=h)], "E. URL が localhost:")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "ABCD"
    for key, fn in (("A", part_a), ("B", part_b), ("C", part_c), ("D", part_d), ("E", part_e)):
        if key in which:
            fn()
