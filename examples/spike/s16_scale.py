"""Spike 16: 第9章の実測。CSV(pandas) と SQLite で、集計・1件検索の時間を、300件と30万件で比べる。"""
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from shared.sales_data import load_sales
from shared.sales_db import SalesRepository, import_dataframe


def timed(fn, repeat=5):
    samples = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples)


def measure(label, df):
    tmp = Path(tempfile.mkdtemp())
    csv = tmp / "sales.csv"
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    t = time.perf_counter()
    import_dataframe(df, tmp / "sales.db")
    import_ms = (time.perf_counter() - t) * 1000
    repo = SalesRepository(tmp / "sales.db")
    target = df["order_id"].iloc[len(df) // 2]
    # 移行前の実装（毎回CSVを読んで pandas で集計・検索）を、指定したCSVで再現する
    read = lambda: pd.read_csv(csv, encoding="utf-8-sig")
    r = {
        "csv_aggregate_ms": timed(lambda: read().groupby("region")["sales_amount"].sum().sort_values(ascending=False)),
        "sql_aggregate_ms": timed(lambda: repo.sales_by("region")),
        "csv_lookup_ms": timed(lambda: (lambda d: d[d["order_id"] == target].iloc[0])(read())),
        "sql_lookup_ms": timed(lambda: repo.get_order(target)),
        "import_ms": import_ms, "csv_kb": csv.stat().st_size // 1024, "db_kb": (tmp / "sales.db").stat().st_size // 1024,
    }
    print(label, len(df), {k: round(v, 2) for k, v in r.items()})


base = load_sales()
measure("small", base)
big = pd.concat([base.assign(order_id=base["order_id"] + f"-{i:04d}") for i in range(1000)], ignore_index=True)
measure("big", big)
