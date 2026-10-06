"""Spike 24: 第15章の実測（PostgreSQL の実機が必要。TEST_DATABASE_URL）。
A. 接続: リクエストごとに接続を作る場合と、プールから借りる場合の、問い合わせ1回あたりの時間。
B. 同時の決定: 承認と却下が同時に来たとき、決定の権利（claim）がない場合に、何が起きるか。
C. プール: 接続の上限より多いスレッドが、同時に問い合わせたときの時間と失敗。
使い方: TEST_DATABASE_URL=postgresql://... uv run python examples/spike/s24_postgres.py [A|B|C]
"""
import collections
import concurrent.futures as cf
import os
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg

import shared.review_flow as rf
from shared.postgres import Database, close_all
from shared.postgres_flow_persistence import PostgresFlowPersistence
from shared.postgres_review import PostgresReviewStore
from shared.postgres_sales import PostgresSalesRepository, import_csv_postgres

URL = os.environ["TEST_DATABASE_URL"]
schema = f"s24_{uuid.uuid4().hex[:8]}"
with psycopg.connect(URL, autocommit=True) as admin:
    admin.execute(f"CREATE SCHEMA {schema}")
db = Database(URL, schema=schema, max_size=8)
import_csv_postgres(db)
SUMMARY = "SELECT COUNT(*), COALESCE(SUM(sales_amount), 0) FROM orders"


def timed(fn, n):
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), max(samples)


def part_a(n=200):
    def new_connection():
        with psycopg.connect(URL, options=f"-c search_path={schema}") as conn:
            conn.execute(SUMMARY).fetchone()

    def pooled():
        with db.connection() as conn:
            conn.execute(SUMMARY).fetchone()

    pooled()
    for label, fn in (("A. 毎回、接続を作る", new_connection), ("A. プールから借りる", pooled)):
        median, worst = timed(fn, n)
        print(f"{label}: 中央値 {median:.2f} ms / 最大 {worst:.1f} ms（{n}回）", flush=True)


def part_b(rounds=20):
    store, persistence = PostgresReviewStore(db), PostgresFlowPersistence(db)
    tickets = [t for t in (rf.start_review(f"O{i:04d}", "user-001", store, persistence) for i in range(4, 60)) if t.status == "pending_approval"][:rounds]
    for label, claim in (("claim なし", False), ("claim あり", True)):
        if not claim:
            original = PostgresReviewStore.claim_decision
            PostgresReviewStore.claim_decision = lambda self, *a, **k: True  # 決定の権利を、常に与える（修正前の動き）
        outcomes = collections.Counter()
        for ticket in tickets[: rounds // 2] if not claim else tickets[rounds // 2:]:
            results, barrier = {}, threading.Barrier(2)

            def go(name, decision):
                barrier.wait()
                try:
                    results[name] = rf.resolve_review(ticket.flow_id, decision, name, store, persistence)
                except ValueError:
                    results[name] = "refused"

            threads = [threading.Thread(target=go, args=("approver-a", "approve")), threading.Thread(target=go, args=("approver-b", "reject"))]
            [t.start() for t in threads]
            [t.join() for t in threads]
            decisions = [e["event"] for e in store.audit_log(ticket.flow_id) if e["event"] in ("approved", "rejected")]
            outcomes[f"決定 {len(decisions)} 件・結果を受け取った人 {sum(r != 'refused' for r in results.values())} 人"] += 1
        if not claim:
            PostgresReviewStore.claim_decision = original
        print(f"B. {label}: {dict(outcomes)}", flush=True)


def part_c():
    small = Database(URL, schema=schema, max_size=4)
    repo = PostgresSalesRepository(small)
    t0 = time.perf_counter()
    errors = 0
    with cf.ThreadPoolExecutor(32) as pool:
        for f in [pool.submit(repo.summary) for _ in range(400)]:
            try:
                f.result()
            except Exception:
                errors += 1
    print(f"C. 接続の上限 4、スレッド 32、400回: {time.perf_counter() - t0:.2f} 秒、失敗 {errors}", flush=True)
    small.close()


mode = sys.argv[1] if len(sys.argv) > 1 else "ABC"
try:
    if "A" in mode:
        part_a()
    if "B" in mode:
        part_b()
    if "C" in mode:
        part_c()
finally:
    close_all()
    db.close()
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
