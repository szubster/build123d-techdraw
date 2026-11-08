"""Tests for multi-page PDF document bookmarks & outline generation."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import BuildSheet, DrawingBooklet, FrontView


def test_booklet_pdf_export_with_bookmarks(tmp_path: Path) -> None:
    """Verify exported PDF contains valid document outline bookmarks matching sheet titles."""
    box = bd.Box(40.0, 30.0, 10.0)
    cyl = bd.Cylinder(radius=15.0, height=20.0)

    with BuildSheet(
        sheet_size="A4",
        drawing_no="DWG-001",
        title="GENERAL ASSEMBLY & KINEMATICS",
    ) as s1:
        FrontView(box, center=(100.0, 100.0))

    with BuildSheet(
        sheet_size="A4",
        drawing_no="DWG-002",
        title="BRACKET DETAIL",
    ) as s2:
        FrontView(cyl, center=(100.0, 100.0))

    booklet = DrawingBooklet(title="PROJECT MASTER BOOKLET")
    booklet.add_sheet(s1.sheet)
    booklet.add_sheet(s2.sheet)

    pdf_file = str(tmp_path / "booklet_with_bookmarks.pdf")
    booklet.export_pdf(pdf_file, bookmarks=True)

    assert Path(pdf_file).exists()
    assert Path(pdf_file).stat().st_size > 0

    with open(pdf_file, encoding="latin1") as f:
        pdf_text = f.read()

    # Verify Catalog links to Outlines
    assert "/Outlines" in pdf_text
    assert "/Type /Outlines" in pdf_text

    # Verify top-level booklet outline entry
    assert "(PROJECT MASTER BOOKLET)" in pdf_text

    # Verify child outline entries for each sheet
    assert "(DWG-001 - GENERAL ASSEMBLY & KINEMATICS)" in pdf_text
    assert "(DWG-002 - BRACKET DETAIL)" in pdf_text


def test_booklet_pdf_export_without_bookmarks(tmp_path: Path) -> None:
    """Verify exported PDF does not generate outline entries when bookmarks=False."""
    box = bd.Box(20.0, 20.0, 10.0)

    with BuildSheet(
        sheet_size="A4",
        drawing_no="DWG-001",
        title="SIMPLE SHEET",
    ) as s:
        FrontView(box, center=(100.0, 100.0))

    booklet = DrawingBooklet(title="NO BOOKMARKS")
    booklet.add_sheet(s.sheet)

    pdf_file = str(tmp_path / "booklet_no_bookmarks.pdf")
    booklet.export_pdf(pdf_file, bookmarks=False)

    with open(pdf_file, encoding="latin1") as f:
        pdf_text = f.read()

    assert "/Type /Outlines" not in pdf_text
