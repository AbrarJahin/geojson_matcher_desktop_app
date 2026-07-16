from __future__ import annotations

import json
from pathlib import Path

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
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
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

    for _, row in pipeline.review_rows().iterrows():
        pipeline.record_manual_decision(str(row["pair_key"]), "no")

    result = pipeline.finalize()
    assert result.county_1_output.exists()
    assert result.county_2_output.exists()
    assert result.final_decisions_csv.exists()
    assert result.decision_audit_csv.exists()
    assert result.connection_audit_csv.exists()
