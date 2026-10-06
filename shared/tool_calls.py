"""呼ばれたToolの名前を記録する（検証用）。本格的な実行追跡は第6章で扱う。"""
from crewai.events import BaseEventListener
from crewai.events.types.tool_usage_events import ToolUsageStartedEvent


class ToolCallRecorder(BaseEventListener):
    def __init__(self) -> None:
        self.calls: list[str] = []
        super().__init__()

    def setup_listeners(self, bus) -> None:
        @bus.on(ToolUsageStartedEvent)
        def _on_tool(source, event) -> None:
            self.calls.append(event.tool_name)

    def clear(self) -> None:
        self.calls.clear()


recorder = ToolCallRecorder()  # イベントバスへの登録は1回だけにするため、モジュールで1つだけ作る
