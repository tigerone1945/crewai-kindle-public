"""Spike共通: .env読み込み・CSV・LLM設定。"""
import os
from pathlib import Path

import pandas as pd
from crewai import LLM
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data" / "ai_agent_practice_sales.csv"

load_dotenv(ROOT / ".env")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")


def get_llm() -> LLM:
    return LLM(model=MODEL, temperature=0)


def load_sales() -> pd.DataFrame:
    return pd.read_csv(CSV_PATH, encoding="utf-8-sig")
