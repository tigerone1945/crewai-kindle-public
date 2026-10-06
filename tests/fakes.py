"""テスト用の偽物（LLMや外部ストレージを使わない）。"""


class FakeMemory:
    def __init__(self, fail_recall=False):
        self.records, self.fail_recall, self.drained, self.closed = [], fail_recall, 0, False
        self.recall_queries = []

    def remember(self, content, scope=None, **kw):
        self.records.append((scope, content))

    def recall(self, query, scope=None, limit=10, **kw):
        self.recall_queries.append(query)
        if self.fail_recall:
            raise ConnectionError("memory backend down")
        hits = [c for s, c in self.records if s == scope][:limit]
        return [type("M", (), {"record": type("R", (), {"content": c})()})() for c in hits]

    def list_records(self, scope=None, **kw):
        return [type("R", (), {"id": f"id{i}", "content": c})() for i, (s, c) in enumerate(self.records) if s == scope]

    def forget(self, scope=None, record_ids=None, **kw):
        before = len(self.records)
        self.records = [(s, c) for i, (s, c) in enumerate(self.records)
                        if not (s == scope and (record_ids is None or f"id{i}" in record_ids))]
        return before - len(self.records)

    def drain_writes(self):
        self.drained += 1

    def close(self):
        self.closed = True
