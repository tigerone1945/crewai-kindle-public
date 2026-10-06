"""付録K の検証用 MCP サーバー（Streamable HTTP + Bearer 認証）。読み取りの Tool だけを公開する。

stdio 版（sales_mcp_server.py）と同じ Tool。違いは、ネットワーク越しに接続されることと、認証の検査が、サーバーの入口にあること。
トークンは、環境変数 MCP_TOKEN で渡す（コードに書かない）。127.0.0.1 だけで待ち受ける。
  MCP_TOKEN=<トークン> uv run python examples/mcp/sales_mcp_http_server.py 18765
"""
import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from shared.join_policy import DIMENSIONS  # noqa: E402
from shared.sales_db import SalesRepository  # noqa: E402

mcp = FastMCP("sales-http")


@mcp.tool()
def sales_by_axis(axis: str) -> str:
    """指定した軸（region / category / customer_type / product_name）の売上と構成比を、売上の大きい順に返す。売上金額には sales_amount を使う。"""
    if axis not in DIMENSIONS:
        raise ValueError(f"axis は {sorted(DIMENSIONS)} のいずれかです")
    rows = SalesRepository.default().sales_by(axis)
    return "\n".join(f"{r[axis]}: {r['sales']:,}円（構成比 {r['share_pct']}%、上位からの累積 {r['cumulative_sales']:,}円・{r['cumulative_share_pct']}%）" for r in rows)


class BearerAuth:
    """純粋な ASGI のミドルウェア。BaseHTTPMiddleware は、長く続く SSE の応答を壊すことがあるため、使わない（Spike s28）。"""

    def __init__(self, app, token: str) -> None:
        self.app, self._token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(header, f"Bearer {self._token}"):  # 定数時間で比べる
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(token: str):
    return BearerAuth(mcp.streamable_http_app(), token)  # lifespan（セッション管理の起動）は、内側のアプリが持つため、そのまま通す


if __name__ == "__main__":
    token = os.environ.get("MCP_TOKEN")
    if not token:
        sys.exit("MCP_TOKEN を環境変数で渡してください")
    uvicorn.run(build_app(token), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 18765, log_level="warning")
