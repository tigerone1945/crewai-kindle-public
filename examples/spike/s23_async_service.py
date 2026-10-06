"""Spike 23: 第14章の実測。
A. 非同期: ブロッキングの処理を、async の関数の中で直接呼ぶ／スレッドに逃がす／同期の関数にする、で他のリクエストがどれだけ待たされるか。
   また、CrewAI の Flow を、イベントループのスレッドで直接呼ぶとどうなるか。
B. 同時実行: 実際の分析サービス（LLM）に、4件を同時に送る。逐次との所要時間の比較と、Trace が実行ごとに混ざらないか。
使い方: s23_async_service.py [A|B]
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from fastapi import FastAPI


def toy_app() -> FastAPI:
    app = FastAPI()

    @app.get("/blocking")
    async def blocking():
        time.sleep(1.5)  # async の中のブロッキング: イベントループが止まる
        return {}

    @app.get("/threaded")
    async def threaded():
        await asyncio.to_thread(time.sleep, 1.5)
        return {}

    @app.get("/sync")
    def sync():
        time.sleep(1.5)  # 同期の関数: FastAPI がスレッドで実行する
        return {}

    @app.get("/health")
    async def health():
        return {}

    return app


async def part_a():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=toy_app()), base_url="http://t") as c:
        for path in ("blocking", "threaded", "sync"):
            t0 = time.perf_counter()  # sleep の前から測る（ブロッキングだと、sleep 自体が長引くため）
            slow = asyncio.create_task(c.get(f"/{path}"))
            await asyncio.sleep(0.2)
            await c.get("/health")
            print(f"A. /{path} の実行中に、/health が返るまで（0.2 秒の待ちを除く）: {time.perf_counter() - t0 - 0.2:.2f} 秒", flush=True)
            await slow

    # Flow を、イベントループのスレッドで直接呼ぶ
    from shared.analysis_flow import run_question
    from shared.review_requests import ReviewStore
    tmp = Path(tempfile.mkdtemp())
    try:
        run_question("O0001をレビュー対象として登録してください。", "user-001", review_store=ReviewStore(tmp / "r.db"), flow_db=tmp / "f.db")
        print("A. Flow をイベントループのスレッドで直接呼んだ: 成功")
    except Exception as e:
        print(f"A. Flow をイベントループのスレッドで直接呼んだ: {type(e).__name__}: {str(e)[:100]}")
    out = await asyncio.to_thread(run_question, "O0001をレビュー対象として登録してください。", "user-001",
                                  review_store=ReviewStore(tmp / "r2.db"), flow_db=tmp / "f2.db")
    print(f"A. Flow をスレッドで呼んだ: {out.kind}")


async def part_b():
    import shared.api as api
    from crewai.memory.unified_memory import Memory

    from shared.memory_policy import PreferenceStore
    from shared.review_requests import ReviewStore
    from shared.sales_data import MODEL

    tmp = Path(tempfile.mkdtemp())
    memory = Memory(llm=MODEL, storage=str(tmp / "memory"))
    store = PreferenceStore(memory)
    store.remember_preference("user-001", "要約は結論から書く")  # 同時の recall でも壊れないかを見る
    deps = api.Dependencies(review_store=ReviewStore(tmp / "r.db"), flow_db=tmp / "f.db", preference_store_factory=lambda: store,
                            trace_dir=tmp / "traces", max_concurrent=4)
    app = api.create_app(deps)
    questions = [("地域別の売上を分析してください。", "get_sales_by_region"), ("カテゴリ別の売上を分析してください。", "get_sales_by_category"),
                 ("商品別の売上を分析してください。", "get_sales_by_product"), ("顧客種別ごとの売上を比較してください。", "get_sales_by_customer_type")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300) as c:
        async def ask(q):
            t0 = time.perf_counter()
            r = await c.post("/v1/analyses", json={"question": q, "actor_id": "user-001"})
            return r, time.perf_counter() - t0

        t0 = time.perf_counter()
        seq = [await ask(q) for q, _ in questions]
        seq_total = time.perf_counter() - t0
        t0 = time.perf_counter()
        par = await asyncio.gather(*(ask(q) for q, _ in questions))
        par_total = time.perf_counter() - t0
    print(f"B. 逐次: {seq_total:.1f} 秒（成功 {sum(r.status_code == 200 for r, _ in seq)}/4）")
    print(f"B. 同時: {par_total:.1f} 秒（成功 {sum(r.status_code == 200 for r, _ in par)}/4）  各リクエスト: {[round(t, 1) for _, t in par]}")
    run_ids = [r.headers["X-Run-Id"] for r, _ in par]
    print(f"B. run_id が重複していない: {len(set(run_ids)) == 4}")
    wanted = {r.headers["X-Run-Id"]: tool for (r, _), (_, tool) in zip(par, questions)}
    for run_id, tool in wanted.items():
        trace = json.loads((tmp / "traces" / f"{run_id}.json").read_text(encoding="utf-8"))
        tools = trace["summary"]["tool_calls"]
        print(f"B. {run_id}: 期待の Tool {tool} → 実行 {tools} / 試行 {trace['summary']['attempts']} / Token {trace['summary']['prompt_tokens'] + trace['summary']['completion_tokens']}")
    memory.close()


mode = sys.argv[1] if len(sys.argv) > 1 else "AB"
if "A" in mode:
    asyncio.run(part_a())
if "B" in mode:
    asyncio.run(part_b())
