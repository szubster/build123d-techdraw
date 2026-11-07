"""Unit tests for advanced view types: Detail Views, Auxiliary Views, and Section Views with background."""

import build123d as bd

from build123d_techdraw.builder import AuxiliaryView, BuildDrawing, DetailView, TopView
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import (
    ViewType,
)


def test_detail_view_creation_and_rendering() -> None:
    """Test creating a circular clipped detail view with callout indicator on parent view."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(60, 40, 20)

    parent_view = sheet.add_view(
        box,
        view_type=ViewType.FRONT,
        center=(80.0, 100.0),
        scale=1.0,
    )

    detail_view = sheet.add_detail_view(
        parent_view=parent_view,
        focus_target=(80.0, 100.0),
        radius=15.0,
        scale=2.0,
        center=(160.0, 100.0),
        view_label="DETAIL A",
        label_letter="A",
    )

    assert detail_view.view_type == ViewType.DETAIL
    assert detail_view.scale == 2.0

    svg = sheet.to_svg()
    # Detail circular boundary and callout indicator
    assert "detail-callout" in svg
    assert "DETAIL A" in svg


def test_auxiliary_view_projection() -> None:
    """Test auxiliary view projected along a custom inclined face."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)

    # Chamfered wedge box to have an inclined face
    box = bd.Box(40, 40, 40)
    chamfered = bd.chamfer(box.edges().filter_by(bd.Axis.Y)[0], length=15)
    inclined_faces = [f for f in chamfered.faces() if 0.1 < abs(f.normal_at().Z) < 0.9]
    face = inclined_faces[0] if inclined_faces else chamfered.faces()[0]

    aux_view = sheet.add_auxiliary_view(
        shape=chamfered,
        face=face,
        center=(140.0, 120.0),
        scale=1.0,
        view_label="VIEW A-A",
    )

    assert aux_view.view_type == ViewType.AUXILIARY
    assert aux_view.svg_content is not None
    assert len(aux_view.svg_content) > 0

    svg = sheet.to_svg()
    assert "VIEW A-A" in svg


def test_section_view_with_background() -> None:
    """Test section view with show_background=True projecting visible geometry behind the section plane."""
    # A block with a step behind the cut
    base = bd.Box(60, 40, 20)
    step = bd.Pos(15, 0, 15) * bd.Box(20, 20, 10)
    compound = base + step

    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)

    view_no_bg = sheet.add_section_view(
        shape=compound,
        cutting_plane=bd.Plane.XZ,
        center=(80.0, 100.0),
        show_background=False,
    )
    svg_no_bg = view_no_bg.svg_content

    view_with_bg = sheet.add_section_view(
        shape=compound,
        cutting_plane=bd.Plane.XZ,
        center=(160.0, 100.0),
        show_background=True,
    )
    svg_with_bg = view_with_bg.svg_content

    svg_sheet = sheet.to_svg()
    assert "sectionHatch" in svg_sheet
    # With background, additional geometry behind the plane is rendered
    assert len(svg_with_bg) > len(svg_no_bg)


def test_builder_detail_and_auxiliary_views() -> None:
    """Test DetailView and AuxiliaryView builder context managers."""
    box = bd.Box(50, 50, 20)
    face = box.faces()[0]

    with BuildDrawing(title="Builder Views") as dwg:
        with TopView(box, center=(80, 100)) as top_v:
            pass

        with DetailView(
            parent_view=top_v,
            focus_target=(80, 100),
            center=(160, 100),
            radius=12.0,
            scale=2.0,
            view_label="DETAIL B",
            label_letter="B",
        ):
            pass

        with AuxiliaryView(
            shape=box,
            face=face,
            center=(80, 180),
            view_label="VIEW C",
        ):
            pass

    svg = dwg.sheet.to_svg()
    assert "DETAIL B" in svg
    assert "VIEW C" in svg
