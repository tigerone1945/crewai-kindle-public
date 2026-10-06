import pytest

from tests.helpers import load_chapter_module

sales_tools = load_chapter_module("ch02-tools", "sales_tools.py")


def test_tools_are_purpose_specific_and_have_no_arbitrary_execution():
    names = {t.name for t in sales_tools.SALES_TOOLS}
    assert names == {"get_sales_summary", "get_sales_by_category", "get_sales_by_region"}
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
