from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from app.core.junctions import (
    JunctionMember,
    JunctionProposal,
    _address_number,
    _normalize_id,
    _robust_center,
    build_junction_proposals,
    classify_attachment,
    format_address_summary,
    junction_signature,
    road_key,
    update_member_geometry,
)


def _member(
    *,
    key: str = "1|1",
    county_index: int = 1,
    road_id: str = "1",
    attachment_type: str = "START",
    measure_m: float = 0.0,
    x: float = 0.0,
    y: float = 0.0,
    selected: bool = True,
) -> JunctionMember:
    return JunctionMember(
        key=key,
        county_index=county_index,
        road_id=road_id,
        road_name=f"Road {road_id}",
        attachment_type=attachment_type,
        part_index=0,
        measure_m=measure_m,
        contact_x=x,
        contact_y=y,
        selected=selected,
    )


def _decision_row(
    pair_key: str,
    county_1_id: int,
    county_2_id: int,
    line_1: LineString,
    line_2: LineString,
    contact_1: Point,
    contact_2: Point,
    probability: float,
) -> dict[str, object]:
    return {
        "pair_key": pair_key,
        "county_1_id": county_1_id,
        "county_2_id": county_2_id,
        "requires_manual_verification": True,
        "geometry_county1": line_1,
        "geometry_county2": line_2,
        "county_1_contact_point": contact_1,
        "county_2_contact_point": contact_2,
        "contact_midpoint": Point(
            (contact_1.x + contact_2.x) / 2.0,
            (contact_1.y + contact_2.y) / 2.0,
        ),
        "probablity": probability,
        "full_road_label_county1": f"A-{county_1_id}",
        "full_road_label_county2": f"B-{county_2_id}",
    }


def test_normalize_id_and_road_key_handle_common_gis_values() -> None:
    assert _normalize_id(pd.NA) == ""
    assert _normalize_id(" 42.0 ") == "42"
    assert _normalize_id(42) == "42"
    assert _normalize_id("A.0") == "A"
    assert road_key(2, " 42.0 ") == "2|42"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (pd.NA, None),
        ("", None),
        ("0", None),
        (float("inf"), None),
        ("101.0", "101"),
        ("101.5", "101.5"),
        ("not-a-number", None),
    ],
)
def test_address_number_normalization(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _address_number(value) == expected


def test_format_address_summary_prefers_source_fields_and_falls_back() -> None:
    row = pd.Series(
        {
            "fromaddr_l_county1": 100,
            "toaddr_l_county1": 198,
            "parity_l_county1": "e",
            "from_right_county1": 101,
            "to_right_county1": 199,
            "parity_right_county1": "o",
        }
    )
    assert format_address_summary(row, 1) == "Addr: L 100–198 E | R 101–199 O"
    assert format_address_summary(pd.Series(dtype=object), 1) == "Addr: —"


def test_classify_attachment_start_end_and_interior() -> None:
    line = LineString([(0, 0), (10, 0)])
    assert classify_attachment(line, Point(0.2, 1), endpoint_tolerance_m=1)[0] == "START"
    end = classify_attachment(line, Point(9.8, 1), endpoint_tolerance_m=1)
    assert end[:3] == ("END", 0, pytest.approx(10.0))
    interior = classify_attachment(line, Point(5, 2), endpoint_tolerance_m=1)
    assert interior[0] == "INTERIOR"
    assert interior[1] == 0
    assert interior[2] == pytest.approx(5.0)
    assert interior[3].equals(Point(5, 0))


def test_classify_attachment_uses_nearest_multiline_part_and_rejects_bad_geometry() -> None:
    geometry = MultiLineString([[(0, 0), (5, 0)], [(100, 0), (110, 0)]])
    result = classify_attachment(geometry, Point(105, 1), endpoint_tolerance_m=0.5)
    assert result[0] == "INTERIOR"
    assert result[1] == 1
    with pytest.raises(ValueError, match="LineString/MultiLineString"):
        classify_attachment(Point(0, 0), Point(0, 0), endpoint_tolerance_m=1)


def test_member_properties_signature_and_state_are_deterministic() -> None:
    first = _member(key="1|1", county_index=1, road_id="1", x=2, y=3)
    second = _member(
        key="2|2",
        county_index=2,
        road_id="2",
        attachment_type="INTERIOR",
        measure_m=12.6,
        x=4,
        y=5,
        selected=False,
    )
    proposal = JunctionProposal(
        junction_id="R1-J0001",
        signature=junction_signature([first, second]),
        round_number=1,
        seed_pair_key="1||2",
        average_probability=0.8,
        pair_keys=["1||2"],
        members=[first, second],
        suggested_x=3,
        suggested_y=4,
        junction_x=3,
        junction_y=4,
    )

    assert first.contact_point.equals(Point(2, 3))
    assert second.attachment_key.endswith("|m15")
    assert proposal.selected_members == [first]
    assert proposal.selected_member_keys == ["1|1"]
    assert junction_signature([second, first]) == proposal.signature
    state = proposal.to_state_dict()
    assert state["junction_id"] == "R1-J0001"
    assert state["members"][1]["selected"] is False


def test_robust_center_uses_medians_and_rejects_empty_input() -> None:
    center = _robust_center(
        [
            _member(key="1|1", x=0, y=0),
            _member(key="1|2", x=100, y=10),
            _member(key="2|3", county_index=2, x=2, y=5),
        ]
    )
    assert center.equals(Point(2, 5))
    with pytest.raises(ValueError, match="at least one member"):
        _robust_center([])


def test_build_junction_proposals_groups_connected_cross_county_roads() -> None:
    a = LineString([(0, 0), (10, 0)])
    b1 = LineString([(0, 0.2), (0, 10)])
    b2 = LineString([(0.3, 0), (10, 10)])
    frame = pd.DataFrame(
        [
            _decision_row("1||101", 1, 101, a, b1, Point(0, 0), Point(0, 0.2), 0.9),
            _decision_row("1||102", 1, 102, a, b2, Point(0, 0), Point(0.3, 0), 0.8),
        ]
    )

    proposals, deferred = build_junction_proposals(
        frame,
        round_number=2,
        group_radius_m=5,
        endpoint_tolerance_m=1,
    )

    assert deferred == 0
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.junction_id == "R2-J0001"
    assert {member.key for member in proposal.members} == {"1|1", "2|101", "2|102"}
    assert proposal.pair_keys == ["1||101", "1||102"]
    assert proposal.average_probability == pytest.approx(0.85)


def test_build_junction_proposals_skips_invalid_rows_exclusions_and_restores_draft() -> None:
    a = LineString([(0, 0), (10, 0)])
    b = LineString([(0, 0), (0, 10)])
    valid = _decision_row("1||101", 1, 101, a, b, Point(0, 0), Point(0, 0), 0.9)
    invalid = dict(valid)
    invalid["pair_key"] = "bad"
    invalid.pop("geometry_county2")
    frame = pd.DataFrame([valid, invalid])

    proposals, _ = build_junction_proposals(
        frame,
        round_number=1,
        group_radius_m=5,
        endpoint_tolerance_m=1,
    )
    assert len(proposals) == 1
    signature = proposals[0].signature

    excluded, _ = build_junction_proposals(
        frame,
        round_number=1,
        group_radius_m=5,
        endpoint_tolerance_m=1,
        excluded_signatures={signature},
    )
    assert excluded == []

    drafts = {
        signature: {
            "selected_member_keys": ["1|1"],
            "junction_x": "1.25",
            "junction_y": "2.5",
            "decision": "reject",
        }
    }
    restored, _ = build_junction_proposals(
        frame,
        round_number=1,
        group_radius_m=5,
        endpoint_tolerance_m=1,
        draft_states=drafts,
    )
    assert restored[0].selected_member_keys == ["1|1"]
    assert restored[0].junction_x == pytest.approx(1.25)
    assert restored[0].junction_y == pytest.approx(2.5)
    assert restored[0].decision == "reject"


def test_build_junction_proposals_returns_empty_when_no_manual_rows() -> None:
    frame = pd.DataFrame([{"requires_manual_verification": False}])
    assert build_junction_proposals(
        frame,
        round_number=1,
        group_radius_m=5,
        endpoint_tolerance_m=1,
    ) == ([], 0)


def test_update_member_geometry_moves_start_and_end_preserving_z() -> None:
    start_line = LineString([(0, 0, 7), (10, 0, 8)])
    start_member = _member(attachment_type="START", x=0, y=0)
    updated, movement = update_member_geometry(start_line, start_member, Point(1, 2))
    assert list(updated.coords)[0] == pytest.approx((1, 2, 7))
    assert movement == pytest.approx(Point(0, 0).distance(Point(1, 2)))

    end_member = _member(attachment_type="END", x=10, y=0)
    updated_end, _ = update_member_geometry(start_line, end_member, Point(11, 3))
    assert list(updated_end.coords)[-1] == pytest.approx((11, 3, 8))


def test_update_member_geometry_inserts_or_replaces_interior_vertex() -> None:
    line = LineString([(0, 0), (10, 0)])
    member = _member(attachment_type="INTERIOR", measure_m=5, x=5, y=0)
    updated, movement = update_member_geometry(line, member, Point(5, 2))
    assert list(updated.coords) == [(0.0, 0.0), (5.0, 2.0), (10.0, 0.0)]
    assert movement == pytest.approx(2.0)

    already_vertex = LineString([(0, 0), (5, 0), (10, 0)])
    replaced, _ = update_member_geometry(already_vertex, member, Point(5, 1))
    assert list(replaced.coords) == [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]


def test_update_member_geometry_preserves_multiline_and_validates_member() -> None:
    multi = MultiLineString([[(0, 0), (10, 0)], [(20, 0), (30, 0)]])
    member = _member(attachment_type="START")
    updated, _ = update_member_geometry(multi, member, Point(1, 1))
    assert updated.geom_type == "MultiLineString"
    assert list(updated.geoms[0].coords)[0] == (1.0, 1.0)

    bad_part = deepcopy(member)
    bad_part.part_index = 99
    with pytest.raises(ValueError, match="part index"):
        update_member_geometry(multi, bad_part, Point(0, 0))

    bad_type = deepcopy(member)
    bad_type.attachment_type = "UNKNOWN"
    with pytest.raises(ValueError, match="Unknown attachment type"):
        update_member_geometry(LineString([(0, 0), (1, 0)]), bad_type, Point(0, 0))
