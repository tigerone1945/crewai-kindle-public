import pytest

from shared import sales_tools


def test_tools_are_purpose_specific_and_have_no_arbitrary_execution():
    names = {t.name for t in sales_tools.SALES_TOOLS}
    assert names == {"get_sales_summary", "get_sales_by_category", "get_sales_by_region", "get_sales_by_customer_type", "get_sales_by_product"}
    assert not any(word in n for n in names for word in ("sql", "python", "exec", "eval"))  # BR-04


def test_every_tool_documents_the_sales_amount_rule():
    for t in sales_tools.SALES_TOOLS:
        assert "sales_amount" in t.description


def test_summary_tool_output():
    out = sales_tools.get_sales_summary.run()
    assert "売上合計: 28,298,695円" in out and "キャンセル件数: 14" in out


def test_region_tool_output_includes_amount_and_share_for_all_regions():
    out = sales_tools.get_sales_by_region.run()
    for region in ("東北", "関西", "九州", "関東", "中部"):
        assert region in out
    assert "東北: 8,439,765円（構成比 29.8%）" in out


def test_customer_type_tool_output_matches_case_06():
    out = sales_tools.get_sales_by_customer_type.run()
    assert "法人: 24,256,195円（構成比 85.7%）" in out and "個人: 4,042,500円（構成比 14.3%）" in out


def test_product_tool_covers_all_products_and_sums_to_the_total():
    out = sales_tools.get_sales_by_product.run()
    amounts = [int(line.split(": ")[1].split("円")[0].replace(",", "")) for line in out.splitlines()]
    assert len(amounts) == 12 and sum(amounts) == 28298695


def test_no_tool_exposes_customer_names():
    """Data Governance: 氏名は Agent から見えない。結合しても、氏名の列は Tool の出力に出ない。"""
    from shared.sales_db import SalesRepository

    names = set(SalesRepository.default().to_dataframe()["customer_name"])
    outputs = " ".join(t.run() for t in sales_tools.SALES_TOOLS)
    assert names and not any(n in outputs for n in names)


def test_llm_has_a_timeout_so_a_silent_provider_cannot_hang_the_crew():
    from shared.sales_data import LLM_TIMEOUT_SECONDS, get_llm

    assert 0 < LLM_TIMEOUT_SECONDS <= 300
    assert get_llm().timeout == LLM_TIMEOUT_SECONDS
