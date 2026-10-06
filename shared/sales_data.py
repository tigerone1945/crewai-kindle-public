"""全章共通: 設定・LLM・売上CSVの読み込み。"""
import os
from pathlib import Path

import pandas as pd
from crewai import LLM
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "ai_agent_practice_sales.csv"

load_dotenv(ROOT / ".env")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))  # 応答が来ないまま、Crew 全体が止まらないように


def get_llm() -> LLM:
    return LLM(model=MODEL, temperature=0, timeout=LLM_TIMEOUT_SECONDS)


def load_sales(path: Path = CSV_PATH) -> pd.DataFrame:
    """売上CSVを読み込む。CSVはBOM付きUTF-8のため utf-8-sig で読む。"""
    if not path.exists():
        raise FileNotFoundError(f"売上CSVが見つかりません: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")
