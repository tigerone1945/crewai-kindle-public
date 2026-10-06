"""Spike 5d: Memory Policy。保存は明示的な remember のみ、Crew は read_only で読むだけ。"""
import re
import tempfile

from crewai.memory.unified_memory import Memory

from common import MODEL
from s05c_preference_memory import has_table, kick  # noqa: E402

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
    writer = Memory(llm=MODEL, storage=d)
    writer.remember("このユーザーは分析結果を必ず表形式で見たい", scope="/user-001/preferences")
    writer.drain_writes()

    reader = Memory(llm=MODEL, storage=d, read_only=True)
    r = kick("地域別の売上を比較してください。", reader)
    print(f"[read_only] table={has_table(r)} | {r[:70]!r}")

    contents = [x.content for x in Memory(llm=MODEL, storage=d).list_records()]
    print("保存件数:", len(contents), "| 売上数値を含む:", any(re.search(r"\d{1,3}(,\d{3})+", c) for c in contents))
