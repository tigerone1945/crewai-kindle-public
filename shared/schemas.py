"""Agentの出力をApplicationとの契約(Contract)として定義する（第3章）。

Schemaは小さく保つ。後続のApplication（UI/API/DB）が実際に使う項目だけを持つ。
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

AnalysisType = Literal[
    "sales_summary", "category_sales", "region_sales", "customer_type_sales", "product_sales", "unsupported"
]


class Metric(BaseModel):
    label: str = Field(description="指標名（例: 東北の売上）")
    value: float = Field(description="値。金額は円、件数は件、構成比は%の数値")
    unit: Literal["円", "件", "%"]


class SalesAnalysisResult(BaseModel):
    question: str
    analysis_type: AnalysisType = Field(
        description="分析種別。対応するToolがない質問は unsupported"
    )
    summary: str = Field(description="分析結果の要約（1〜2文）")
    metrics: list[Metric] = Field(description="Toolの結果から引用した数値。自分で計算した数値は入れない")
    insights: list[str] = Field(description="重要な発見（2〜4件）")
    recommendations: list[str] = Field(description="次に確認すべきこと（1〜3件）")

    @model_validator(mode="after")
    def _check_contract(self) -> "SalesAnalysisResult":
        if self.analysis_type == "unsupported":
            if self.metrics:
                raise ValueError("unsupported のとき metrics は空にする")
            return self
        if not self.metrics:
            raise ValueError("metrics が空です")
        if not 2 <= len(self.insights) <= 4:
            raise ValueError("insights は2〜4件にする")
        if not 1 <= len(self.recommendations) <= 3:
            raise ValueError("recommendations は1〜3件にする")
        return self
