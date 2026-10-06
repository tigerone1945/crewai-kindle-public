"""Kindle 向けの EPUB の組み立て（book/build_epub.py）のテスト。pandoc が要るテストは、無ければスキップ。"""
import importlib.util
import shutil
import zipfile

import pytest

from tests.helpers import ROOT

spec = importlib.util.spec_from_file_location("build_epub", ROOT / "book" / "build_epub.py")
build_epub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_epub)


def test_headings_are_shifted_but_not_inside_code_fences():
    text = "# 章\n\n## 節\n\n```bash\n# コメント\n## これも\n```\n\n### 小節\n"
    out = build_epub.shift_headings(text)
    assert "## 章" in out and "### 節" in out and "#### 小節" in out
    assert "\n# コメント\n" in out and "\n## これも\n" in out  # コードの中は、そのまま


def test_headings_never_go_below_level_six():
    assert build_epub.shift_headings("###### 深い\n").startswith("###### ")


TABLE = "| 名前 | a | b | c | d | e |\n|---|---|---|---|---|---|\n| x | 1 | 2 | 3 | 4 | 5 |\n| y | 6 |  | 8 | 9 | 10 |\n"


def test_wide_tables_become_lists_and_narrow_tables_stay():
    out = build_epub.wide_tables_to_lists("前\n\n" + TABLE + "\n後\n")
    assert "- **x**: a 1、b 2、c 3、d 4、e 5" in out and "- **y**: a 6、c 8、d 9、e 10" in out  # 空のセルは、省く
    assert "|" not in out and "前" in out and "後" in out
    narrow = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert build_epub.wide_tables_to_lists(narrow).strip() == narrow.strip()


def test_tables_inside_code_fences_are_not_touched():
    text = "```text\n" + TABLE + "```\n"
    assert build_epub.wide_tables_to_lists(text).strip() == text.strip()


def test_parts_cover_every_chapter_exactly_once_in_order():
    chapters = [n for _, _, rng in build_epub.PARTS for n in rng]
    assert chapters == list(range(1, 19))
    assert all(build_epub.chapter_file(n).exists() for n in chapters)


def test_composed_book_has_every_piece_in_reading_order():
    book = build_epub.compose("# 奥付\n\n版\n")
    order = [book.index(m) for m in ("# はじめに", "# 第1部", "## 第1章", "# 第2部", "## 第9章", "# 第6部", "## 第18章", "# 終章", "# 付録", "## 付録K", "# 奥付")]
    assert order == sorted(order)
    assert [l for l in book.splitlines() if l.startswith("# 第1部")] == ["# 第1部　Agent Fundamentals"] and "![図" in book


def test_every_image_in_the_manuscript_is_resolvable_from_the_build_directory():
    import re
    for path in build_epub.MANUSCRIPT.glob("*.md"):
        for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            assert (build_epub.MANUSCRIPT / ref).resolve().exists(), (path.name, ref)


def test_metadata_has_the_fields_the_colophon_needs():
    meta = build_epub.read_metadata(ROOT / "book" / "metadata.yaml")
    assert {"title", "author", "rights", "edition"} <= set(meta) and "CrewAI" in meta["title"]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc が必要")
def test_epub_builds_and_passes_the_structure_check(tmp_path):
    epub = build_epub.build(tmp_path / "book.epub")
    assert build_epub.inspect(epub) == []
    with zipfile.ZipFile(epub) as z:
        nav = z.read("EPUB/nav.xhtml").decode("utf-8")
        assert "第18章" in nav and "付録K" in nav and "奥付" in nav
        assert sum(n.startswith("EPUB/media/") for n in z.namelist()) == 12


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc が必要")
def test_structure_check_detects_a_missing_image(tmp_path):
    epub = build_epub.build(tmp_path / "book.epub")
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(epub) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            if item.filename != "EPUB/media/file0.jpg":
                dst.writestr(item, src.read(item.filename))
    assert any("画像がありません" in p for p in build_epub.inspect(broken))


WIDE = "```text\n" + "画面 → check_question → run_analysis → Crew → Tool（読み取りのみ）→ " * 2 + "\n```\n"
NARROW = "```text\nA → B\n```\n"


def test_wide_diagrams_are_replaced_and_narrow_ones_stay(tmp_path):
    fonts = build_epub.find_font("BOOK_MONO_FONT", build_epub.MONO_FONT_CANDIDATES) and build_epub.find_font("BOOK_CJK_FONT", build_epub.CJK_FONT_CANDIDATES)
    out = build_epub.diagrams_to_images(NARROW + "\n" + WIDE, tmp_path)
    assert NARROW in out  # 狭い図は、文字のまま
    if fonts:
        assert "![文字で描いた図を、画像にしたもの](diagram-" in out and len(list(tmp_path.glob("diagram-*.png"))) == 1
    else:
        assert WIDE in out  # フォントが無ければ、文字のまま（壊さない）


def test_without_fonts_diagrams_stay_as_text(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOK_MONO_FONT", "/no/such/font")
    monkeypatch.setattr(build_epub, "MONO_FONT_CANDIDATES", [])
    assert build_epub.diagrams_to_images(WIDE, tmp_path) == WIDE and not list(tmp_path.iterdir())


def test_markdown_build_keeps_tables_and_resolves_every_image(tmp_path):
    import re
    out = build_epub.build_markdown(tmp_path / "out" / "book.md")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\ntitle:") and "# 目次" in text
    assert "  - 第18章　EvaluationとRegressionで改善Loopを作る" in text.split("# はじめに\n\n## ")[0]
    assert "| sequential | 6/6 | 6/6 | 3 | 0 | 6,489 | 16.7 秒 |" in text  # 広い表も、変換しない
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert len(refs) == 12 and all((out.parent / r).resolve().exists() for r in refs)
    toc = build_epub.table_of_contents("# 部\n\n## 章\n\n```bash\n# コメント\n```\n")
    assert toc == "- 部\n  - 章"
