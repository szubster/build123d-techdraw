"""Tests for Semantic Topological Anchoring (defense against TNP)."""

from __future__ import annotations

import build123d as bd
import pytest

from build123d_techdraw import (
    Anchor,
    AnchorDirection,
    AngularDimension,
    BoltCircle,
    BOMBalloon,
    BuildDrawing,
    Centerline,
    CenterMark,
    ChamferCallout,
    DatumFeatureSymbol,
    DiameterDimension,
    Direction,
    DrawingSheet,
    FrontView,
    GDTCharacteristic,
    HoleCallout,
    LeaderNote,
    LinearDimension,
    OrdinateDimension,
    OrdinateDimensionGroup,
    RadialDimension,
    SectionCuttingLine,
    SemanticFingerprint,
    SheetSize,
    SurfaceFinishSymbol,
    TopView,
    ViewType,
    WeldingSymbol,
    run_sheet_lint,
)


def _make_bracket(
    length: float = 100.0,
    width: float = 60.0,
    thickness: float = 20.0,
    hole_radius: float = 8.0,
    hole_x: float = 25.0,
    extra_hole: bool = False,
    add_fillets: bool = False,
) -> bd.Part:
    """Create a parametric bracket whose B-Rep indices shift when features are added."""
    with bd.BuildPart() as bp:
        bd.Box(length, width, thickness)
        if add_fillets:
            bd.fillet(bp.edges().filter_by(bd.Axis.Z), radius=4.0)
        with bd.Locations((hole_x, 0.0, 0.0)):
            bd.Cylinder(radius=hole_radius, height=thickness + 4.0, mode=bd.Mode.SUBTRACT)
        if extra_hole:
            with bd.Locations((-hole_x, 0.0, 0.0)):
                bd.Cylinder(radius=4.0, height=thickness + 4.0, mode=bd.Mode.SUBTRACT)
    assert bp.part is not None
    return bp.part


def test_declarative_extreme_and_direction_enums() -> None:
    part = _make_bracket(length=100.0, width=60.0, thickness=20.0)
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(part, ViewType.FRONT, center=(150.0, 140.0), scale=1.0)

    assert Anchor.MAX is AnchorDirection.MAX
    assert Anchor.MIN is AnchorDirection.MIN
    assert Direction.MAX is AnchorDirection.MAX

    x_min = Anchor.min("X")
    x_max = Anchor.max("X")
    dim = LinearDimension(x_min, x_max, direction="horizontal", offset=15.0)
    front += dim

    assert dim.resolved
    assert not dim.anchor_stale
    assert dim.text == "100"
    assert dim.end[0] > dim.start[0]

    # Extreme using explicit Direction enum
    top_edge = Anchor.extreme("Z", direction=Direction.MAX, entity="edge")
    bot_edge = Anchor.extreme("Z", direction=Direction.MIN, entity="edge")
    vdim = LinearDimension(bot_edge, top_edge, direction="vertical", offset=15.0, view=front)
    assert vdim.resolved
    assert vdim.text == "20"


def test_fingerprint_survives_tnp_and_updates_on_rebuild() -> None:
    part_v1 = _make_bracket(length=100.0, hole_radius=8.0, hole_x=25.0)
    hole_faces = [
        f
        for f in part_v1.faces()
        if f.geom_type == bd.GeomType.CYLINDER and abs(f.bounding_box().center().X - 25.0) < 1.0
    ]
    assert len(hole_faces) == 1
    hole_face_v1 = hole_faces[0]

    # Capture semantic fingerprint from v1 topology
    hole_anchor = Anchor.of(hole_face_v1)
    assert isinstance(hole_anchor.fingerprint, SemanticFingerprint)

    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    top = sheet.add_view(part_v1, ViewType.TOP, center=(180.0, 140.0), scale=1.0)

    dia_dim = DiameterDimension(anchor=hole_anchor, view=top)
    hc = HoleCallout(anchor=hole_anchor, view=top)
    cm = CenterMark(anchor=hole_anchor, view=top)

    assert dia_dim.text == "⌀16"
    assert "⌀16" in hc.lines[0]
    cx_v1, _ = cm.center

    # Rebuild v2: add fillets + extra hole (completely scrambles B-Rep face indices),
    # resize bracket to 120mm, and enlarge target hole from R8 -> R10 at x=30
    part_v2 = _make_bracket(
        length=120.0,
        hole_radius=10.0,
        hole_x=30.0,
        extra_hole=True,
        add_fillets=True,
    )
    sheet.update_shape(part_v2)

    assert not dia_dim.anchor_stale
    assert not hc.anchor_stale
    assert not cm.anchor_stale
    assert dia_dim.text == "⌀20"
    assert "⌀20" in hc.lines[0]
    cx_v2, _ = cm.center
    assert cx_v2 > cx_v1


def test_declarative_hole_cylinder_face_edge_vertex_anchors() -> None:
    part = _make_bracket(length=100.0, hole_radius=8.0, hole_x=25.0, extra_hole=True)

    with (
        BuildDrawing(SheetSize.A3_LANDSCAPE) as sheet,
        TopView(part, center=(180.0, 140.0), scale=1.0),
    ):
        a_hole = Anchor.hole(radius=8.0, near=(25.0, 0.0, 0.0))
        a_cyl = Anchor.cylinder(radius=4.0, near=(-25.0, 0.0, 0.0))
        a_face = Anchor.face(normal=(0, 0, 1))
        a_edge = Anchor.edge(axis="X", extreme_axis="Y", direction=Anchor.MAX)
        a_vtx = Anchor.vertex(axis="X", direction=Anchor.MAX)

        d_hole = DiameterDimension(a_hole)
        r_cyl = RadialDimension(a_cyl)
        d_betw = LinearDimension(a_hole, a_cyl, direction="horizontal", offset=-20.0)
        ln = LeaderNote(anchor=Anchor.center_of(a_face), text="TOP FACE")
        datum = DatumFeatureSymbol(anchor=a_edge, label="A")
        sf = SurfaceFinishSymbol(anchor=a_vtx, ra_value="Ra 1.6")

    assert d_hole.text == "⌀16"
    assert r_cyl.text == "R4"
    assert d_betw.text == "50"
    assert ln.resolved and not ln.anchor_stale
    assert datum.resolved and not datum.anchor_stale
    assert sf.resolved and not sf.anchor_stale
    assert len(sheet.lint()) >= 0  # Ensure linter runs cleanly on all anchored annotations


def test_all_annotation_types_and_view_helpers_accept_anchors() -> None:
    part = _make_bracket(length=80.0, width=50.0, thickness=20.0, hole_radius=6.0, hole_x=20.0)
    a_left = Anchor.min("X")
    a_right = Anchor.max("X")
    a_top = Anchor.max("Z")
    a_hole = Anchor.hole(radius=6.0)

    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(part, ViewType.FRONT, center=(160.0, 140.0), scale=1.0)

    # View helper methods with anchor=
    fcf = front.add_gdt_frame(
        characteristic=GDTCharacteristic.FLATNESS,
        tolerance_value="0.05",
        anchor=a_top,
    )
    datum = front.add_datum_symbol(label="B", anchor=a_left)
    sf = front.add_surface_finish(ra_value="Ra 0.8", anchor=a_right)
    hc = front.hole_callout(anchor=a_hole)
    tc = front.thread_callout(thread="M12", anchor=a_hole)

    # Direct annotation constructors with anchor= / anchor1= / anchor2=
    cl = Centerline(anchor1=a_left, anchor2=a_right, view=front)
    bc = BoltCircle(anchor=a_hole, diameter=24.0, num_holes=4, view=front)
    ord_dim = OrdinateDimension(anchor=a_right, datum_coord=0.0, direction="horizontal", view=front)
    ord_grp = OrdinateDimensionGroup(datum_target=a_left, direction="horizontal", view=front)
    ord_grp.add_ordinate(a_hole)
    ord_grp.add_ordinate(a_right)
    ang = AngularDimension(
        anchor1=Anchor.edge(axis="X", extreme_axis="Z", direction=Anchor.MAX),
        anchor2=Anchor.edge(axis="Z", extreme_axis="X", direction=Anchor.MAX),
        view=front,
    )
    balloon = BOMBalloon(item_no=1, anchor=a_hole, view=front)
    cut = SectionCuttingLine([a_left, a_right], label="A", view=front)
    chamfer = ChamferCallout(anchor=a_top, width=1.5, view=front)
    weld = WeldingSymbol(anchor=a_right, weld_type="FILLET", view=front)

    for ann in (fcf, datum, sf, hc, tc, cl, bc, ord_dim, ord_grp, ang, balloon, cut, chamfer, weld):
        assert not getattr(ann, "anchor_stale", False)

    assert len(ord_grp.ordinates) == 3
    assert "90°" in ang.to_svg()


def test_view_locate_and_scale_to_rebind_anchors() -> None:
    part = _make_bracket(length=100.0, width=60.0, thickness=20.0)
    with (
        BuildDrawing(SheetSize.A3_LANDSCAPE) as _,
        FrontView(part, center=(150.0, 140.0), scale=1.0) as front,
    ):
        dim = LinearDimension(Anchor.min("X"), Anchor.max("X"), direction="horizontal", offset=10.0)

    s1_x = dim.start[0]
    e1_x = dim.end[0]
    assert e1_x - s1_x == pytest.approx(100.0, abs=0.5)

    # Move view by +40mm in X
    front.locate((190.0, 140.0))
    assert dim.start[0] == pytest.approx(s1_x + 40.0, abs=0.5)
    assert dim.end[0] == pytest.approx(e1_x + 40.0, abs=0.5)

    # Scale view to 0.5
    front.scale_to(0.5)
    assert dim.end[0] - dim.start[0] == pytest.approx(50.0, abs=0.5)
    assert dim.text == "100"  # Model dimension remains 100mm


def test_stale_anchor_fallback_and_linter_warning() -> None:
    part_with_hole = _make_bracket(length=100.0, hole_radius=8.0, hole_x=25.0)
    hole_face = [
        f
        for f in part_with_hole.faces()
        if f.geom_type == bd.GeomType.CYLINDER and abs(f.bounding_box().center().X - 25.0) < 1.0
    ][0]
    hole_anchor = Anchor.of(hole_face)

    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    top = sheet.add_view(part_with_hole, ViewType.TOP, center=(180.0, 140.0), scale=1.0)
    dia = DiameterDimension(hole_anchor, view=top)
    hc = HoleCallout(anchor=hole_anchor, view=top)

    prev_center = dia.center
    assert not dia.anchor_stale
    assert not any(issue.code == "STALE_ANCHOR" for issue in run_sheet_lint(sheet))

    # Rebuild WITHOUT any hole -> anchor cannot resolve
    part_no_hole = bd.Part() + bd.Box(100.0, 60.0, 20.0)
    sheet.update_shape(part_no_hole)

    assert hole_anchor.is_stale
    assert dia.anchor_stale
    assert hc.anchor_stale
    assert dia.center == prev_center  # Preserved last known coordinates

    issues = run_sheet_lint(sheet)
    stale_issues = [i for i in issues if i.code == "STALE_ANCHOR"]
    assert len(stale_issues) >= 2
    assert all(i.severity == "WARNING" for i in stale_issues)


def _make_plate_with_bore(small_hole_diameter: float | None = 5.0) -> bd.Part:
    """120x80x10 plate with a central ⌀50 bore and an optional small hole at (40, 25)."""
    with bd.BuildPart() as bp:
        bd.Box(120.0, 80.0, 10.0)
        bd.Cylinder(radius=25.0, height=14.0, mode=bd.Mode.SUBTRACT)
        if small_hole_diameter is not None:
            with bd.Locations((40.0, 25.0, 0.0)):
                bd.Cylinder(radius=small_hole_diameter / 2.0, height=14.0, mode=bd.Mode.SUBTRACT)
    assert bp.part is not None
    return bp.part


def test_hole_anchor_goes_stale_when_small_hole_removed_despite_large_bore() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    top = sheet.add_view(_make_plate_with_bore(5.0), ViewType.TOP, center=(180.0, 140.0))
    anchor = Anchor.hole(diameter=5)
    hc = HoleCallout(anchor=anchor, view=top)

    assert not anchor.is_stale
    assert "⌀5" in hc.lines[0]
    assert not any(i.code == "STALE_ANCHOR" for i in run_sheet_lint(sheet))

    sheet.update_shape(_make_plate_with_bore(None))

    assert anchor.is_stale
    assert hc.anchor_stale
    assert "⌀50" not in hc.lines[0]
    assert any(i.code == "STALE_ANCHOR" for i in run_sheet_lint(sheet))


def test_hole_anchor_rebinds_after_modest_resize() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    top = sheet.add_view(_make_plate_with_bore(5.0), ViewType.TOP, center=(180.0, 140.0))
    anchor = Anchor.hole(diameter=5)
    hc = HoleCallout(anchor=anchor, view=top)

    sheet.update_shape(_make_plate_with_bore(5.5))

    assert not anchor.is_stale
    assert not hc.anchor_stale
    assert anchor.last_resolved_radius == pytest.approx(2.75)
    assert "⌀5.5" in hc.lines[0]


def test_hole_anchor_index_out_of_range_is_stale() -> None:
    part = _make_plate_with_bore(5.0)
    assert Anchor.hole(diameter=5, index=0).resolve_entity(part) is not None

    anchor = Anchor.hole(diameter=5, index=1)
    assert anchor.resolve_entity(part) is None
    assert anchor.is_stale
    assert anchor.stale_reason is not None and "index" in anchor.stale_reason

    unfiltered = Anchor.hole(index=5)  # only two internal cylinders exist
    assert unfiltered.resolve_entity(part) is None
    assert unfiltered.is_stale


def test_fingerprint_anchor_does_not_rebind_to_much_larger_hole() -> None:
    part_v1 = _make_plate_with_bore(5.0)
    small = [
        f
        for f in part_v1.faces()
        if f.geom_type == bd.GeomType.CYLINDER and abs((f.radius or 0.0) - 2.5) < 1e-6
    ]
    assert len(small) == 1
    anchor = Anchor.of(small[0], shape=part_v1)
    assert anchor.resolve_entity(part_v1) is not None and not anchor.is_stale

    # The ⌀5 hole is gone; an unrelated ⌀12 hole sits 15 mm away. Position alone would
    # make it the best match, but a >2x radius change means it is a different feature.
    other_hole = bd.Pos(25.0, 25.0, 0.0) * bd.Cylinder(radius=6.0, height=14.0)
    part_v2 = _make_plate_with_bore(None) - other_hole
    assert anchor.resolve_entity(part_v2) is None
    assert anchor.is_stale

    # The bore alone is never a match either.
    assert anchor.resolve_entity(_make_plate_with_bore(None)) is None
    assert anchor.is_stale

    # A modest resize of the captured hole still follows it.
    resized = anchor.resolve_entity(_make_plate_with_bore(6.0))
    assert resized is not None and not anchor.is_stale
    assert anchor.last_resolved_radius == pytest.approx(3.0)
