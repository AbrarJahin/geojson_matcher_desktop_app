from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point


@dataclass(slots=True)
class JunctionMember:
    key: str
    county_index: int
    road_id: str
    road_name: str
    attachment_type: str
    part_index: int
    measure_m: float
    contact_x: float
    contact_y: float
    selected: bool = True

    @property
    def contact_point(self) -> Point:
        return Point(self.contact_x, self.contact_y)

    @property
    def attachment_key(self) -> str:
        # Endpoint identity is stable. Interior identity uses a 5 m measure bucket
        # so the same road can still participate at a distinct interior junction.
        if self.attachment_type == "INTERIOR":
            bucket = int(round(float(self.measure_m) / 5.0) * 5)
            location = f"m{bucket}"
        else:
            location = self.attachment_type
        return f"{self.key}|part{self.part_index}|{location}"


@dataclass(slots=True)
class JunctionProposal:
    junction_id: str
    signature: str
    round_number: int
    seed_pair_key: str
    average_probability: float
    pair_keys: list[str]
    members: list[JunctionMember]
    suggested_x: float
    suggested_y: float
    junction_x: float
    junction_y: float
    decision: str | None = None

    @property
    def selected_members(self) -> list[JunctionMember]:
        return [member for member in self.members if member.selected]

    @property
    def selected_member_keys(self) -> list[str]:
        return [member.key for member in self.members if member.selected]

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "signature": self.signature,
            "round_number": int(self.round_number),
            "seed_pair_key": self.seed_pair_key,
            "average_probability": float(self.average_probability),
            "pair_keys": list(self.pair_keys),
            "members": [asdict(member) for member in self.members],
            "suggested_x": float(self.suggested_x),
            "suggested_y": float(self.suggested_y),
            "junction_x": float(self.junction_x),
            "junction_y": float(self.junction_y),
            "decision": self.decision,
        }


def _normalize_id(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    try:
        number = float(text)
        if np.isfinite(number) and number.is_integer():
            return str(int(number))
    except Exception:
        pass
    return text.replace(".0", "")


def road_key(county_index: int, road_id: Any) -> str:
    return f"{int(county_index)}|{_normalize_id(road_id)}"


def _parts(geometry: Any) -> list[LineString]:
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type == "MultiLineString":
        return list(geometry.geoms)
    return []


def classify_attachment(
    geometry: Any,
    contact_point: Point,
    *,
    endpoint_tolerance_m: float,
) -> tuple[str, int, float, Point]:
    parts = _parts(geometry)
    if not parts:
        raise ValueError("Only non-empty LineString/MultiLineString roads are supported.")

    part_index = min(
        range(len(parts)),
        key=lambda index: float(parts[index].distance(contact_point)),
    )
    line = parts[part_index]
    coordinates = list(line.coords)
    if len(coordinates) < 2:
        raise ValueError("A road line contains fewer than two coordinates.")

    projected_measure = float(line.project(contact_point))
    projected_point = line.interpolate(projected_measure)
    start = Point(coordinates[0][:2])
    end = Point(coordinates[-1][:2])
    start_distance = float(start.distance(projected_point))
    end_distance = float(end.distance(projected_point))

    if min(start_distance, end_distance) <= float(endpoint_tolerance_m):
        if start_distance <= end_distance:
            return "START", part_index, 0.0, start
        return "END", part_index, float(line.length), end

    return "INTERIOR", part_index, projected_measure, projected_point


def _member_name(row: pd.Series, county_index: int, road_id: str) -> str:
    suffix = "county1" if county_index == 1 else "county2"
    return str(
        row.get(f"full_road_label_{suffix}")
        or row.get(f"road_name_{suffix}")
        or road_id
    )


def _edge_from_row(
    row: pd.Series,
    *,
    endpoint_tolerance_m: float,
) -> dict[str, Any]:
    id_1 = _normalize_id(row["county_1_id"])
    id_2 = _normalize_id(row["county_2_id"])
    key_1 = road_key(1, id_1)
    key_2 = road_key(2, id_2)
    contact_1 = row["county_1_contact_point"]
    contact_2 = row["county_2_contact_point"]
    midpoint = row.get("contact_midpoint")
    if not isinstance(midpoint, Point):
        midpoint = Point(
            (float(contact_1.x) + float(contact_2.x)) / 2.0,
            (float(contact_1.y) + float(contact_2.y)) / 2.0,
        )

    attachment_1 = classify_attachment(
        row["geometry_county1"],
        contact_1,
        endpoint_tolerance_m=endpoint_tolerance_m,
    )
    attachment_2 = classify_attachment(
        row["geometry_county2"],
        contact_2,
        endpoint_tolerance_m=endpoint_tolerance_m,
    )

    member_1 = JunctionMember(
        key=key_1,
        county_index=1,
        road_id=id_1,
        road_name=_member_name(row, 1, id_1),
        attachment_type=attachment_1[0],
        part_index=attachment_1[1],
        measure_m=attachment_1[2],
        contact_x=float(attachment_1[3].x),
        contact_y=float(attachment_1[3].y),
    )
    member_2 = JunctionMember(
        key=key_2,
        county_index=2,
        road_id=id_2,
        road_name=_member_name(row, 2, id_2),
        attachment_type=attachment_2[0],
        part_index=attachment_2[1],
        measure_m=attachment_2[2],
        contact_x=float(attachment_2[3].x),
        contact_y=float(attachment_2[3].y),
    )

    try:
        probability = float(row.get("probablity", np.nan))
    except Exception:
        probability = math.nan

    return {
        "pair_key": str(row["pair_key"]),
        "road_keys": (key_1, key_2),
        "midpoint": midpoint,
        "contacts": (contact_1, contact_2),
        "members": (member_1, member_2),
        "probability": probability,
    }


def junction_signature(members: Iterable[JunctionMember]) -> str:
    attachment_keys = sorted(member.attachment_key for member in members)
    payload = json.dumps(attachment_keys, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _robust_center(members: Iterable[JunctionMember]) -> Point:
    member_list = list(members)
    if not member_list:
        raise ValueError("A junction must contain at least one member.")
    xs = [member.contact_x for member in member_list]
    ys = [member.contact_y for member in member_list]
    return Point(float(np.median(xs)), float(np.median(ys)))


def build_junction_proposals(
    decision_frame: pd.DataFrame,
    *,
    round_number: int,
    group_radius_m: float,
    endpoint_tolerance_m: float,
    excluded_signatures: set[str] | None = None,
    draft_states: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[JunctionProposal], int]:
    """Build one conflict-free junction review round from pair-level ML evidence.

    The scientific pipeline remains pair based. This layer converts every
    MANUAL_REVIEW pair into a graph edge, expands spatially coherent candidate
    junctions, de-duplicates them, then greedily selects the highest-average-
    probability groups so no road appears in two junctions in the same round.

    Returns ``(selected_for_this_round, deferred_conflict_count)``.
    """
    excluded = excluded_signatures or set()
    drafts = draft_states or {}

    manual = decision_frame.loc[
        decision_frame["requires_manual_verification"].astype(bool)
    ].copy()
    if manual.empty:
        return [], 0

    edges: list[dict[str, Any]] = []
    for _, row in manual.iterrows():
        try:
            edge = _edge_from_row(
                row,
                endpoint_tolerance_m=endpoint_tolerance_m,
            )
        except (KeyError, TypeError, ValueError):
            continue
        edges.append(edge)

    if not edges:
        return [], 0

    by_road: dict[str, list[int]] = {}
    for index, edge in enumerate(edges):
        for key in edge["road_keys"]:
            by_road.setdefault(key, []).append(index)

    def edge_is_spatially_compatible(edge: dict[str, Any], anchor: Point) -> bool:
        if float(edge["midpoint"].distance(anchor)) > group_radius_m:
            return False
        return all(
            float(contact.distance(anchor)) <= group_radius_m
            for contact in edge["contacts"]
        )

    candidates: dict[str, JunctionProposal] = {}
    seed_order = sorted(
        range(len(edges)),
        key=lambda index: (
            -(
                edges[index]["probability"]
                if math.isfinite(edges[index]["probability"])
                else -1.0
            ),
            edges[index]["pair_key"],
        ),
    )

    for seed_index in seed_order:
        seed = edges[seed_index]
        anchor = seed["midpoint"]
        included_edges: set[int] = {seed_index}
        roads: set[str] = set(seed["road_keys"])
        frontier = list(roads)

        while frontier:
            current = frontier.pop()
            for edge_index in by_road.get(current, []):
                edge = edges[edge_index]
                if edge_index in included_edges:
                    continue
                if not edge_is_spatially_compatible(edge, anchor):
                    continue
                included_edges.add(edge_index)
                for road in edge["road_keys"]:
                    if road not in roads:
                        roads.add(road)
                        frontier.append(road)

        member_options: dict[str, list[JunctionMember]] = {road: [] for road in roads}
        for edge_index in included_edges:
            edge = edges[edge_index]
            for member in edge["members"]:
                if member.key in roads:
                    member_options[member.key].append(member)

        members: list[JunctionMember] = []
        for key in sorted(roads):
            options = member_options.get(key, [])
            if not options:
                continue
            # Use the contact/attachment nearest to the seed anchor for this
            # particular physical-junction hypothesis.
            chosen = min(
                options,
                key=lambda member: float(member.contact_point.distance(anchor)),
            )
            if float(chosen.contact_point.distance(anchor)) <= group_radius_m:
                members.append(chosen)

        county_set = {member.county_index for member in members}
        if len(members) < 2 or county_set != {1, 2}:
            continue

        member_keys = {member.key for member in members}
        internal_edges = [
            edges[index]
            for index in included_edges
            if set(edges[index]["road_keys"]).issubset(member_keys)
        ]
        probabilities = [
            edge["probability"]
            for edge in internal_edges
            if math.isfinite(edge["probability"])
        ]
        average_probability = float(np.mean(probabilities)) if probabilities else 0.0
        signature = junction_signature(members)
        if signature in excluded:
            continue

        center = _robust_center(members)
        pair_keys = sorted({edge["pair_key"] for edge in internal_edges})
        proposal = JunctionProposal(
            junction_id="",
            signature=signature,
            round_number=int(round_number),
            seed_pair_key=str(seed["pair_key"]),
            average_probability=average_probability,
            pair_keys=pair_keys,
            members=members,
            suggested_x=float(center.x),
            suggested_y=float(center.y),
            junction_x=float(center.x),
            junction_y=float(center.y),
        )

        prior = candidates.get(signature)
        if prior is None or proposal.average_probability > prior.average_probability:
            candidates[signature] = proposal

    ordered = sorted(
        candidates.values(),
        key=lambda proposal: (
            -proposal.average_probability,
            -len(proposal.members),
            proposal.signature,
        ),
    )

    selected: list[JunctionProposal] = []
    used_roads: set[str] = set()
    deferred = 0
    for proposal in ordered:
        proposal_roads = {member.key for member in proposal.members}
        if proposal_roads & used_roads:
            deferred += 1
            continue
        used_roads.update(proposal_roads)
        selected.append(proposal)

    for index, proposal in enumerate(selected, start=1):
        proposal.junction_id = f"R{round_number}-J{index:04d}"
        draft = drafts.get(proposal.signature)
        if not draft:
            continue
        selected_keys = set(draft.get("selected_member_keys") or [])
        if selected_keys:
            for member in proposal.members:
                member.selected = member.key in selected_keys
        try:
            proposal.junction_x = float(draft.get("junction_x", proposal.junction_x))
            proposal.junction_y = float(draft.get("junction_y", proposal.junction_y))
        except (TypeError, ValueError):
            pass
        decision = str(draft.get("decision") or "").strip().lower()
        proposal.decision = decision if decision in {"accept", "reject"} else None

    return selected, deferred


def update_member_geometry(
    geometry: Any,
    member: JunctionMember,
    junction_point: Point,
) -> tuple[Any, float]:
    """Return geometry with this member attached exactly to ``junction_point``."""
    parts = _parts(geometry)
    if not parts:
        raise ValueError("Only LineString/MultiLineString roads can be updated.")
    if member.part_index < 0 or member.part_index >= len(parts):
        raise ValueError("Saved junction part index is no longer valid.")

    line = parts[member.part_index]
    coordinates = list(line.coords)
    if len(coordinates) < 2:
        raise ValueError("A road line contains fewer than two coordinates.")

    def replacement_coordinate(original: tuple[Any, ...]) -> tuple[Any, ...]:
        replacement: tuple[Any, ...] = (float(junction_point.x), float(junction_point.y))
        if len(original) >= 3:
            replacement += tuple(original[2:])
        return replacement

    if member.attachment_type == "START":
        original_point = Point(coordinates[0][:2])
        coordinates[0] = replacement_coordinate(coordinates[0])
    elif member.attachment_type == "END":
        original_point = Point(coordinates[-1][:2])
        coordinates[-1] = replacement_coordinate(coordinates[-1])
    elif member.attachment_type == "INTERIOR":
        measure = float(np.clip(member.measure_m, 0.0, float(line.length)))
        original_point = line.interpolate(measure)

        # Locate the segment containing the saved projected measure and insert a
        # shared vertex. This preserves both road endpoints for T-junctions.
        running = 0.0
        insert_at = len(coordinates) - 1
        for index in range(len(coordinates) - 1):
            a = Point(coordinates[index][:2])
            b = Point(coordinates[index + 1][:2])
            segment_length = float(a.distance(b))
            if measure <= running + segment_length + 1e-9:
                insert_at = index + 1
                break
            running += segment_length

        replacement = replacement_coordinate(coordinates[max(insert_at - 1, 0)])
        # If the projected point was already a vertex, replace it rather than
        # creating an unnecessary duplicate coordinate.
        nearest_vertex_index = min(
            range(len(coordinates)),
            key=lambda index: Point(coordinates[index][:2]).distance(original_point),
        )
        if Point(coordinates[nearest_vertex_index][:2]).distance(original_point) <= 1e-7:
            coordinates[nearest_vertex_index] = replacement_coordinate(
                coordinates[nearest_vertex_index]
            )
        else:
            coordinates.insert(insert_at, replacement)
    else:
        raise ValueError(f"Unknown attachment type: {member.attachment_type}")

    updated_line = LineString(coordinates)
    parts[member.part_index] = updated_line
    updated_geometry = updated_line if geometry.geom_type == "LineString" else MultiLineString(parts)
    movement_m = float(original_point.distance(junction_point))
    return updated_geometry, movement_m
