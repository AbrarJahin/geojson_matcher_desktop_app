from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.core.pipeline import PipelineConfig, RoadMatchingPipeline


def _write_roads(path: Path, start_id: int, y_offset: float) -> None:
    features = []
    for index in range(6):
        x = -86.10 + index * 0.002
        coordinates = [[x, 39.92 + y_offset], [x + 0.0015, 39.92 + y_offset]]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "OBJECTID": start_id + index,
                    "st_name": f"Road {index}",
                    "st_postyp": "ST",
                    "roadclass": "LOCAL",
                    "oneway": "N",
                    "speedlimit": 30,
                    "geofromleft": 100 + index * 10,
                    "geotoleft": 108 + index * 10,
                    "geofromright": 101 + index * 10,
                    "geotoright": 109 + index * 10,
                    "geoparityleft": "E",
                    "geoparityright": "O",
                    "geocityleft": "INDIANAPOLIS",
                    "geocityright": "INDIANAPOLIS",
                    "geozipl": "46200",
                    "geozipr": "46200",
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        )
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )


def test_pipeline_runs_on_synthetic_geojson(tmp_path: Path) -> None:
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    _write_roads(first, 1, 0.0)
    _write_roads(second, 101, 0.00005)
    pipeline = RoadMatchingPipeline(
        PipelineConfig(first, second, tmp_path / "output", buffer_distance_meters=80.0)
    )
    pipeline.run_analysis()
    summary = pipeline.review_summary()
    assert summary["total_candidates"] > 0
    assert summary["selected"] >= 1
    assert 0.0 <= summary["threshold"] <= 1.0

    session_path = pipeline.session_path
    assert not session_path.exists()

    for _, row in pipeline.review_rows().iterrows():
        pipeline.record_manual_decision(str(row["pair_key"]), "no")
        # The core requirement: each click changes only RAM.
        assert not session_path.exists()

    saved = pipeline.save_session(force=True, reason="test quit")
    assert saved == session_path
    assert session_path.exists()
    progress = pd.read_csv(session_path)
    assert progress["manual_decision"].dropna().eq("no").all()

    resumed = RoadMatchingPipeline(
        PipelineConfig(first, second, tmp_path / "output", buffer_distance_meters=80.0)
    )
    resumed.run_analysis()
    resumed_summary = resumed.review_summary()
    assert resumed.loaded_session_path == session_path
    assert resumed_summary["completed"] == resumed_summary["selected"]
    assert resumed_summary["remaining"] == 0

    result = resumed.finalize()
    assert result.county_1_output.exists()
    assert result.county_2_output.exists()
    assert result.final_decisions_csv.exists()
    assert result.decision_audit_csv.exists()
    assert result.connection_audit_csv.exists()


def test_strong_direct_evidence_protects_border_continuation_from_safe_reject(
    tmp_path: Path,
) -> None:
    def feature(
        object_id: int,
        name: str,
        posttype: str,
        coordinates: list[list[float]],
        address_start: int,
        address_end: int,
        city: str,
    ) -> dict[str, object]:
        return {
            "type": "Feature",
            "properties": {
                "OBJECTID": object_id,
                "st_name": name,
                "st_postyp": posttype,
                "roadclass": "Local",
                "oneway": None,
                "speedlimit": 35,
                "geofromleft": address_start,
                "geotoleft": address_end,
                "geofromright": address_start + 1,
                "geotoright": address_end + 1,
                "geoparityleft": "E",
                "geoparityright": "O",
                "geocityleft": city,
                "geocityright": city,
                "geozipl": "46280",
                "geozipr": "46280",
            },
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

    hamilton = tmp_path / "Hamilton_crop.geojson"
    marion = tmp_path / "Marion_crop.geojson"

    hamilton_features = [
        feature(
            1,
            "96th",
            "Street",
            [[-86.145888239, 39.927175471], [-86.143980702, 39.927148681]],
            700,
            798,
            "Indianapolis",
        ),
        feature(
            2,
            "96th",
            "Street",
            [[-86.147465266, 39.927157015], [-86.145888239, 39.927175471]],
            604,
            698,
            "Indianapolis",
        ),
        feature(
            3,
            "College",
            "Avenue",
            [[-86.145888239, 39.927175471], [-86.145892285, 39.928043281]],
            9600,
            9606,
            "Carmel",
        ),
    ]
    marion_features = [
        feature(
            1,
            "96th",
            "Street",
            [[-86.147533344, 39.927136538], [-86.145877663, 39.927158806]],
            600,
            698,
            "Indianapolis",
        ),
        feature(
            2,
            "College",
            "Avenue",
            [[-86.145875034, 39.925298772], [-86.145877663, 39.927158806]],
            9500,
            9598,
            "Indianapolis",
        ),
        feature(
            3,
            "96th",
            "Street",
            [
                [-86.145877663, 39.927158806],
                [-86.143982266, 39.927141055],
                [-86.141841726, 39.927122885],
            ],
            700,
            1026,
            "Indianapolis",
        ),
    ]

    hamilton.write_text(
        json.dumps({"type": "FeatureCollection", "features": hamilton_features}),
        encoding="utf-8",
    )
    marion.write_text(
        json.dumps({"type": "FeatureCollection", "features": marion_features}),
        encoding="utf-8",
    )

    pipeline = RoadMatchingPipeline(
        PipelineConfig(hamilton, marion, tmp_path / "output")
    ).run_analysis()
    decisions = pipeline.decision_df

    college_continuation = decisions.loc[
        (decisions["OBJECTID_county1"] == 3)
        & (decisions["OBJECTID_county2"] == 2)
    ].iloc[0]

    assert college_continuation["geometric_rule_probability"] > 0.95
    assert college_continuation["geometric_valid_pair_probability"] < 0.75
    assert college_continuation["textual_valid_pair_probability"] > 0.95
    assert college_continuation["probablity"] < pipeline.optimized_threshold
    assert bool(college_continuation["safe17_strong_direct_agreement"])
    assert college_continuation["safe_reject_decision"] == "MANUAL_REVIEW"
    assert (
        college_continuation["safe_reject_reason"]
        == "manual_review_strong_direct_geometry_text_agreement"
    )

    cross_street = decisions.loc[
        decisions["name_1"].str.contains("96TH")
        != decisions["name_2"].str.contains("96TH")
    ]
    assert len(cross_street) == 4
    assert cross_street["safe_reject_decision"].eq("SAFE_REJECT").all()

    diagnostics = pipeline.manual_review_diagnostics()
    assert diagnostics["selected"] == 5
    assert diagnostics["safe_rejected"] == 4
