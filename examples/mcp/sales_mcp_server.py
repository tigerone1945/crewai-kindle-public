"""付録K の検証用 MCP サーバー（stdio）。売上の集計（読み取り）と、レビュー依頼の登録（副作用）を、MCP の Tool として公開する。

本書のサービスには、組み込まない。CrewAI が MCP の Tool を、どう発見し、実行するかを確かめるための、最小の実装。
- sales_by_axis: 読み取り専用。軸は、許可されたものだけ（BR-04）
- create_review_request: 副作用。注文IDの形式・実在・重複を、サーバー側で検査する（Agent 側の検査に頼らない: 多層防御）
登録先は、環境変数 REVIEW_DB（一時ファイル）。開発用の DB には、書かない。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from shared.join_policy import DIMENSIONS  # noqa: E402
from shared.review_requests import DuplicateRequest, InvalidReviewTarget, ReviewStore, validate_review_target  # noqa: E402
from shared.sales_db import SalesRepository  # noqa: E402

mcp = FastMCP("sales")


@mcp.tool()
def sales_by_axis(axis: str) -> str:
    """指定した軸（region / category / customer_type / product_name）の売上と構成比を、売上の大きい順に返す。売上金額には sales_amount を使う。"""
    if axis not in DIMENSIONS:
        raise ValueError(f"axis は {sorted(DIMENSIONS)} のいずれかです")
    rows = SalesRepository.default().sales_by(axis)
    return "\n".join(f"{r[axis]}: {r['sales']:,}円（構成比 {r['share_pct']}%、上位からの累積 {r['cumulative_sales']:,}円・{r['cumulative_share_pct']}%）" for r in rows)


@mcp.tool()
def create_review_request(order_id: str, requested_by: str) -> str:
    """注文をレビュー対象として登録する。副作用がある（登録される）。"""
    try:
        validate_review_target(order_id)  # 形式と実在
        ReviewStore(os.environ["REVIEW_DB"]).create_request(order_id, requested_by, "mcp-direct", "mcp-direct")
    except (InvalidReviewTarget, DuplicateRequest) as e:
        raise ValueError(e.message if hasattr(e, "message") else str(e)) from None
    return f"{order_id} を登録しました"


if __name__ == "__main__":
    mcp.run(transport="stdio")
