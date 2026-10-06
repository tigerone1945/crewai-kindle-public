"""Agentの出力した数値が、実データから決定論的に計算した値と一致するかを検査する。

形式が正しい（Schemaに適合する）ことと、内容が正しいことは別である。
この検査は、LLMが自分で計算した数値や、存在しない数値を混ぜていないかを突き止める（BR-03）。
"""
import re

from shared.join_policy import DIMENSIONS
from shared.sales_db import SalesRepository
from shared.schemas import SalesAnalysisResult

PCT_TOLERANCE = 0.05


class SnapshotRepository:
    """1回の実行の間、同じ問い合わせの結果を使い回す。検査は、1回の実行で、何度も（検査のたびに、軸の数だけ）同じ集計を取り直していた。

    集計が遅い・負荷が高いとき、検査が回答より長くかかる（Spike s25: Tool 4秒の遅れが、検査の中の取り直しで、全体 42 秒になった）。
    1つの実行の中の検査は、同じ時点のデータで行う方が、基準もぶれない。次の実行では、作り直す。
    """

    def __init__(self, repo: SalesRepository) -> None:
        self._repo = repo
        self._cache: dict[tuple, object] = {}

    def _cached(self, key: tuple, load):
        if key not in self._cache:
            self._cache[key] = load()
        return self._cache[key]

    def summary(self):
        return self._cached(("summary",), self._repo.summary)

    def data_quality(self):
        return self._cached(("data_quality",), self._repo.data_quality)

    def sales_by(self, dimension: str):
        return self._cached(("sales_by", dimension), lambda: self._repo.sales_by(dimension))


def legitimate_values(repo: SalesRepository) -> dict[str, set[float]]:
    """Toolが返しうる数値を、単位別に集める。Tool と同じ Repository から取るため、正解が食い違わない。"""
    summary = repo.summary()
    quality = repo.data_quality()  # データ品質担当の Tool が返しうる値（件数・割合）も、正しい値として認める
    amounts = {float(summary["total_sales"])}
    counts = {float(summary["order_count"]), float(summary["cancelled_count"]), float(summary["review_count"]),
              float(quality["sales_exceeds_total_count"]), float(quality["cancelled_with_sales_count"]),
              float(quality["orders_without_customer_or_product"])}
    percents: set[float] = {float(quality["cancelled_share_pct"]), float(quality["review_share_pct"])}
    for dimension in DIMENSIONS:  # Tool が返しうる軸すべて（結合を伴う軸を含む）
        for row in repo.sales_by(dimension):
            amounts.add(float(row["sales"]))
            percents.add(float(row["share_pct"]))
            amounts.add(float(row["cumulative_sales"]))
            percents.add(float(row["cumulative_share_pct"]))  # 「上位2つで○円・○%」は、Tool が返す累積の値だけを認める
    return {"円": amounts, "件": counts, "%": percents}


def find_ungrounded_metrics(result: SalesAnalysisResult, repo: SalesRepository) -> list[str]:
    """実データに裏づけのないmetricを、日本語のメッセージで返す。空なら問題なし。"""
    allowed = legitimate_values(repo)
    problems = []
    for m in result.metrics:
        tolerance = PCT_TOLERANCE if m.unit == "%" else 0
        if not any(abs(m.value - v) <= tolerance for v in allowed[m.unit]):
            problems.append(f"{m.label}: {m.value:,.10g}{m.unit} は実データから得られる値ではありません")
    return problems


_AMOUNT_IN_TEXT = re.compile(r"\d{1,3}(?:,\d{3})+")
_PERCENT_IN_TEXT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def find_ungrounded_numbers_in_text(result: SalesAnalysisResult, repo: SalesRepository) -> list[str]:
    """summary / insights / recommendations の文中に現れる金額(桁区切り)と%が、実データの値かを検査する。

    metricsだけを検査しても、文章に混ざった数値は見逃す。ただし文中の数値の抽出は完全ではない
    （桁区切りのない数値や、言い換えた数値は対象外）。
    """
    allowed = legitimate_values(repo)
    all_numbers = allowed["円"] | allowed["件"]
    problems = []
    for text in [result.summary, *result.insights, *result.recommendations]:
        for raw in _AMOUNT_IN_TEXT.findall(text):
            if float(raw.replace(",", "")) not in all_numbers:
                problems.append(f"文中の金額 {raw} は実データから得られる値ではありません: {text[:30]}…")
        for raw in _PERCENT_IN_TEXT.findall(text):
            if not any(abs(float(raw) - v) <= PCT_TOLERANCE for v in allowed["%"]):
                problems.append(f"文中の {raw}% は実データから得られる値ではありません: {text[:30]}…")
    return problems
