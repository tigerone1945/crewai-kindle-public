"""第3章 Structured Outputで分析結果を安定させる。

Taskの出力をPydanticのSchemaで受け取り、Applicationが扱える契約にする。
Agent/Task/Crewの定義と検証は shared/analysis_service.py にある（第4章以降の画面からも使う）。
使い方: uv run python chapters/ch03-structured-output/main.py "地域別の売上を分析してください。"
"""
import sys
from pathlib import Path

# 章のフォルダー名にハイフンがあり `python -m` で実行できないため、リポジトリのルートを import 対象に加える
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.analysis_service import run_analysis  # noqa: E402

DEFAULT_QUESTION = "地域別の売上を分析してください。"


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    print(run_analysis(question).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
