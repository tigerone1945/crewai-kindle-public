"""テスト共通: ハイフンを含む章フォルダーのモジュールを読み込む。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_chapter_module(chapter_dir: str, filename: str):
    chapter_path = ROOT / "chapters" / chapter_dir
    sys.path.insert(0, str(chapter_path))  # 章内の他モジュール（例: sales_tools）をimportできるようにする
    try:
        spec = importlib.util.spec_from_file_location(f"{chapter_dir}.{filename[:-3]}", chapter_path / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(chapter_path))
    return module


def analyze_with_retry(service, question, attempts=2, **kwargs):
    """run_analysis を、検証を通らなかった場合に限り、全体をもう一度だけ実行する。

    run_analysis は内部で3回試すが、LLMの出力が確率的なため、まれに（第3章の測定で1/20程度）失敗する。
    それはサービスの契約どおりの結果で、統合テストが確かめたい内容（好みの反映など）とは別の話なので、
    テストの不安定さとしては吸収する。サービス自体の失敗率は第3章で測定している。
    """
    for i in range(attempts):
        try:
            return service.run_analysis(question, **kwargs)
        except service.AnalysisError:
            if i == attempts - 1:
                raise
