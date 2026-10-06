"""Memory Policy（第5章）: Agentの長期記憶に「何を保存してよいか」を決める。

方針（Spike s05c/s05d）:
- 保存してよいのは、利用者の好み（Preference）だけ。形式・表現・よく使う分析軸など。
- 売上などのBusiness Factは保存しない。変化する値はDatabase(CSV)を正本とし、毎回Toolで再計算する（BR-07/08）。
- Secret・個人情報は保存しない（BR-10）。
- 保存は明示的な remember() だけで行う。Crewの自動Memory（Crew(memory=...)）は使わない。
  自動Memoryは、Toolが返した売上数値まで保存したため。
- Memoryは品質を上げる補助であり、取得に失敗してもAgentは動く。
"""
import logging
import re
from typing import Protocol

from shared.sensitive import contains_personal, contains_secret

logger = logging.getLogger(__name__)

MAX_PREFERENCE_LENGTH = 200
_ACTOR_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_BUSINESS_FACT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:万円|円|件|%|％)")


class PolicyViolation(ValueError):
    """Memory Policyに反する保存要求。"""


def check_preference_text(text: str) -> str:
    """保存してよい好みなら、整えた文字列を返す。反するなら PolicyViolation。"""
    text = text.strip()
    if not text:
        raise PolicyViolation("内容が空です")
    if len(text) > MAX_PREFERENCE_LENGTH:
        raise PolicyViolation(f"{MAX_PREFERENCE_LENGTH}文字以内で入力してください")
    if _BUSINESS_FACT.search(text):
        raise PolicyViolation("売上などの数値は保存できません（数値は毎回データから再計算します）")
    if contains_secret(text):
        raise PolicyViolation("パスワードやAPIキーなどの秘密情報は保存できません")
    if contains_personal(text):
        raise PolicyViolation("メールアドレスや電話番号などの個人情報は保存できません")
    return text


def check_actor_id(actor_id: str) -> str:
    if not _ACTOR_ID.match(actor_id):
        raise ValueError("actor_id は英数字・ハイフン・アンダースコアの1〜64文字にしてください")
    return actor_id


def preference_scope(actor_id: str) -> str:
    return f"/{check_actor_id(actor_id)}/preferences"


class MemoryLike(Protocol):  # crewai.memory.unified_memory.Memory が満たす最小のインターフェース
    def remember(self, content: str, scope: str | None = ..., **kwargs): ...
    def recall(self, query: str, scope: str | None = ..., **kwargs): ...
    def list_records(self, scope: str | None = ..., **kwargs): ...
    def forget(self, scope: str | None = ..., **kwargs) -> int: ...
    def drain_writes(self) -> None: ...
    def close(self) -> None: ...


class PreferenceStore:
    """利用者ごとの好みを、Scope（/<actor_id>/preferences）で分けて保存・取得・削除する。"""

    def __init__(self, memory: MemoryLike) -> None:
        self._memory = memory

    def remember_preference(self, actor_id: str, text: str) -> None:
        text = check_preference_text(text)
        self._memory.remember(text, scope=preference_scope(actor_id), categories=["preference"])
        self._memory.drain_writes()  # 書き込みを確定させる

    def recall_preferences(self, actor_id: str, query: str, limit: int = 3) -> list[str]:
        """関連する好みを最大limit件返す。取得に失敗しても空で返す（Memoryは補助）。"""
        scope = preference_scope(actor_id)
        try:
            matches = self._memory.recall(query, scope=scope, limit=limit, depth="shallow")
            return [m.record.content for m in matches]
        except Exception:
            logger.exception("memory recall failed: scope=%s", scope)
            return []

    def list_preferences(self, actor_id: str) -> list[tuple[str, str]]:
        records = self._memory.list_records(scope=preference_scope(actor_id))
        return [(r.id, r.content) for r in records]

    def delete_preference(self, actor_id: str, record_id: str) -> int:
        return self._memory.forget(scope=preference_scope(actor_id), record_ids=[record_id])

    def close(self) -> None:
        """Memoryのストレージを閉じる。スクリプトの終了時に呼ぶ（閉じないとLanceDBが終了時に警告を出す）。"""
        self._memory.close()

    def delete_all(self, actor_id: str) -> int:
        """利用者の好みをすべて削除する（削除要求への対応）。"""
        return self._memory.forget(scope=preference_scope(actor_id))


def default_preference_store() -> PreferenceStore:
    from crewai.memory.unified_memory import Memory

    from shared.sales_data import MODEL, ROOT

    return PreferenceStore(Memory(llm=MODEL, storage=str(ROOT / "data" / "memory")))
