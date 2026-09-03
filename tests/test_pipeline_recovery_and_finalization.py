from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

import app.core.pipeline as pipeline_module
from app.core.junctions import JunctionMember, JunctionProposal
from app.core.pipeline import PipelineConfig, RoadMatchingPipeline


def _write_geojson(path: Path, road_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"OBJECTID": road_id},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-86.15, 39.92], [-86.149, 39.92]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _pipeline(tmp_path: Path) -> RoadMatchingPipeline:
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    _write_geojson(first, 1)
    _write_geojson(second, 101)
    return RoadMatchingPipeline(PipelineConfig(first, second, tmp_path / "output"))


def _decision_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pair_key": "1||101",
                "county_1_id": "1",
                "county_2_id": "101",
                "manual_pair_number": 1,
                "manual_batch_id": 1,
                "manual_review_rank": 1,
                "requires_manual_verification": True,
                "manual_decision": pd.NA,
                "safe_reject_decision": "MANUAL_REVIEW",
                "probablity": 0.9,
            },
            {
                "pair_key": "2||102",
                "county_1_id": "2",
                "county_2_id": "102",
                "manual_pair_number": pd.NA,
                "manual_batch_id": pd.NA,
                "manual_review_rank": 2,
                "requires_manual_verification": False,
                "manual_decision": pd.NA,
                "safe_reject_decision": "SAFE_REJECT",
                "probablity": 0.1,
            },
        ]
    )


def _ready(pipeline: RoadMatchingPipeline) -> RoadMatchingPipeline:
    pipeline.config.output_dir.mkdir(parents=True, exist_ok=True)
    pipeline._state_dir.mkdir(parents=True, exist_ok=True)
    pipeline._build_session_identity()
    pipeline._analysis_complete = True
    pipeline.namespace = {
        "section_24_decision_df": _decision_frame(),
        "SAFE17_GLOBAL_THRESHOLD": 0.5,
    }
    return pipeline


def _proposal() -> JunctionProposal:
    members = [
        JunctionMember(
            key="1|1",
            county_index=1,
            road_id="1",
            road_name="Alpha Road",
            attachment_type="START",
            part_index=0,
            measure_m=0.0,
            contact_x=500000.0,
            contact_y=4400000.0,
        ),
        JunctionMember(
            key="2|101",
            county_index=2,
            road_id="101",
            road_name="Beta Road",
            attachment_type="START",
            part_index=0,
            measure_m=0.0,
            contact_x=500000.0,
            contact_y=4400001.0,
        ),
    ]
    return JunctionProposal(
        junction_id="R1-J0001",
        signature="original-signature",
        round_number=1,
        seed_pair_key="1||101",
        average_probability=0.9,
        pair_keys=["1||101", "malformed-pair-key"],
        members=members,
        suggested_x=500000.0,
        suggested_y=4400000.5,
        junction_x=500000.0,
        junction_y=4400000.5,
    )


def test_missing_working_geojson_archives_state_and_restarts_from_originals(
    tmp_path: Path,
) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline._state_dir.mkdir(parents=True)
    pipeline._build_session_identity()
    pipeline._session_csv_path.write_text("stale progress", encoding="utf-8")
    _write_geojson(pipeline._working_file_1, 1)
    pipeline._junction_state_path.write_text(
        json.dumps(
            {
                "schema_version": pipeline_module.JUNCTION_STATE_SCHEMA_VERSION,
                "session_signature": pipeline._session_signature,
                "junction_round": 4,
                "accepted_junctions": [{"signature": "old-accepted-junction"}],
            }
        ),
        encoding="utf-8",
    )

    namespace = pipeline._build_namespace()

    assert pipeline.session_recovery_warning is not None
    assert pipeline._working_file_2.name in pipeline.session_recovery_warning
    assert pipeline.loaded_session_path is None
    assert pipeline._restart_from_original_inputs is True
    assert pipeline._accepted_junction_history == []
    assert namespace["COUNTY_FILE_1"] == str(pipeline.config.county_file_1)
    assert namespace["COUNTY_FILE_2"] == str(pipeline.config.county_file_2)
    assert namespace["MANUAL_PROGRESS_CSV"] == pipeline._session_csv_path
    assert not pipeline._junction_state_path.exists()
    assert not pipeline._session_csv_path.exists()
    assert not pipeline._working_file_1.exists()
    archived = list((pipeline._state_dir / "unusable_sessions").glob("*/*"))
    assert {path.name for path in archived} == {
        pipeline._junction_state_path.name,
        pipeline._session_csv_path.name,
        pipeline._working_file_1.name,
    }


def test_junction_state_loader_handles_invalid_draft_and_complete_states(
    tmp_path: Path,
) -> None:
    unreadable = _pipeline(tmp_path / "unreadable")
    unreadable._state_dir.mkdir(parents=True)
    unreadable._build_session_identity()
    unreadable._junction_state_path.write_text("not json", encoding="utf-8")
    unreadable._load_junction_state()
    assert unreadable.loaded_session_path is None
    unreadable._load_junction_state()  # A state is loaded at most once per pipeline.

    incompatible = _pipeline(tmp_path / "incompatible")
    incompatible._state_dir.mkdir(parents=True)
    incompatible._build_session_identity()
    incompatible._junction_state_path.write_text(
        json.dumps({"schema_version": -1}), encoding="utf-8"
    )
    incompatible._load_junction_state()
    assert incompatible.loaded_session_path is None

    changed = _pipeline(tmp_path / "changed")
    changed._state_dir.mkdir(parents=True)
    changed._build_session_identity()
    changed._junction_state_path.write_text(
        json.dumps(
            {
                "schema_version": pipeline_module.JUNCTION_STATE_SCHEMA_VERSION,
                "session_signature": "different",
            }
        ),
        encoding="utf-8",
    )
    changed._load_junction_state()
    assert changed.loaded_session_path is None

    drafts = _pipeline(tmp_path / "drafts")
    drafts._state_dir.mkdir(parents=True)
    drafts._build_session_identity()
    drafts._junction_state_path.write_text(
        json.dumps(
            {
                "schema_version": pipeline_module.JUNCTION_STATE_SCHEMA_VERSION,
                "session_signature": drafts._session_signature,
                "junction_round": 0,
                "rejected_signatures": ["rejected"],
                "resolved_signatures": ["resolved"],
                "accepted_pair_keys": ["1||101"],
                "accepted_junctions": [],
                "current_round_drafts": ["invalid draft container"],
            }
        ),
        encoding="utf-8",
    )
    drafts._load_junction_state()
    assert drafts.loaded_session_path == drafts._junction_state_path
    assert drafts._junction_round == 1
    assert drafts._junction_drafts == {}
    assert drafts._rejected_junction_signatures == {"rejected"}
    assert drafts._resolved_junction_signatures == {"resolved"}
    assert drafts._accepted_pair_keys == {"1||101"}

    complete = _pipeline(tmp_path / "complete")
    complete._state_dir.mkdir(parents=True)
    complete._build_session_identity()
    _write_geojson(complete._working_file_1, 1)
    _write_geojson(complete._working_file_2, 101)
    complete._junction_state_path.write_text(
        json.dumps(
            {
                "schema_version": pipeline_module.JUNCTION_STATE_SCHEMA_VERSION,
                "session_signature": complete._session_signature,
                "junction_round": 3,
                "accepted_junctions": [{"signature": "accepted"}],
            }
        ),
        encoding="utf-8",
    )
    complete._load_junction_state()
    assert complete.loaded_session_path == complete._junction_state_path
    assert complete._analysis_input_files() == (
        complete._working_file_1,
        complete._working_file_2,
    )


def test_progress_compatibility_covers_corrupt_legacy_and_source_cases(
    tmp_path: Path,
) -> None:
    pipeline = _ready(_pipeline(tmp_path))
    assert pipeline._progress_file_is_compatible(tmp_path / "missing.csv") is False

    progress = pipeline._manual_progress_frame().drop(columns=["session_signature"])
    legacy = tmp_path / "legacy.csv"
    progress.to_csv(legacy, index=False)
    assert pipeline._progress_file_is_compatible(legacy) is True

    empty_source = progress.copy()
    empty_source["source_file_1"] = pd.NA
    empty_source_path = tmp_path / "empty-source.csv"
    empty_source.to_csv(empty_source_path, index=False)
    assert pipeline._progress_file_is_compatible(empty_source_path) is False

    wrong_second = progress.copy()
    wrong_second["source_file_2"] = str(tmp_path / "Other.geojson")
    wrong_second_path = tmp_path / "wrong-second.csv"
    wrong_second.to_csv(wrong_second_path, index=False)
    assert pipeline._progress_file_is_compatible(wrong_second_path) is False


def test_atomic_csv_replace_reports_persistent_lock_and_cleans_temp(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pipeline_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("locked")))
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)
    target = tmp_path / "locked.csv"

    with pytest.raises(PermissionError, match="Close it in Excel"):
        RoadMatchingPipeline._atomic_replace_csv(pd.DataFrame({"a": [1]}), target)

    assert list(tmp_path.glob(".locked.csv.*.tmp")) == []


def test_logger_failure_is_detached_and_ram_progress_message_is_safe(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    calls: list[str] = []

    def broken_logger(message: str) -> None:
        calls.append(message)
        raise RuntimeError("closed UI")

    pipeline.set_logger(broken_logger)
    pipeline.log("first")
    pipeline.log("second")
    pipeline._ram_only_progress_stub()
    assert calls == ["first"]


def test_apply_accepted_junction_writes_both_working_geojson_files(tmp_path: Path) -> None:
    pipeline = _ready(_pipeline(tmp_path))
    line_1 = LineString([(500000.0, 4400000.0), (500010.0, 4400000.0)])
    line_2 = LineString([(500000.0, 4400001.0), (500000.0, 4400010.0)])
    pipeline.namespace.update(
        {
            "_normalize_id": lambda value: str(value).replace(".0", ""),
            "marion_gdf": gpd.GeoDataFrame(
                {"OBJECTID": [1], "geometry": [line_1]},
                geometry="geometry",
                crs=pipeline.config.target_crs,
            ),
            "hamilton_gdf": gpd.GeoDataFrame(
                {"OBJECTID": [101], "geometry": [line_2]},
                geometry="geometry",
                crs=pipeline.config.target_crs,
            ),
        }
    )
    proposal = _proposal()

    pipeline._apply_accepted_junctions([proposal])

    assert pipeline._working_file_1.is_file()
    assert pipeline._working_file_2.is_file()
    assert pipeline._accepted_pair_keys == {"1||101"}
    assert len(pipeline._accepted_junction_history) == 1
    history = pipeline._accepted_junction_history[0]
    assert history["selected_pair_keys"] == ["1||101"]
    assert len(history["member_audit"]) == 2
    first_geometry = json.loads(
        pipeline._working_file_1.read_text(encoding="utf-8")
    )["features"][0]["geometry"]
    second_geometry = json.loads(
        pipeline._working_file_2.read_text(encoding="utf-8")
    )["features"][0]["geometry"]
    assert first_geometry["coordinates"][0] == pytest.approx(
        second_geometry["coordinates"][0]
    )


def test_complete_junction_round_covers_pending_accept_and_reject_paths(
    monkeypatch, tmp_path: Path
) -> None:
    pending_pipeline = _ready(_pipeline(tmp_path / "pending"))
    pending_pipeline._junctions = [_proposal()]
    with pytest.raises(ValueError, match="remain unanswered"):
        pending_pipeline.complete_junction_round()

    accepted_pipeline = _ready(_pipeline(tmp_path / "accepted"))
    accepted = _proposal()
    accepted.decision = "accept"
    rejected = _proposal()
    rejected.signature = "rejected-signature"
    rejected.decision = "reject"
    accepted_pipeline._junctions = [accepted, rejected]
    calls: list[str] = []
    monkeypatch.setattr(
        accepted_pipeline,
        "_apply_accepted_junctions",
        lambda proposals: calls.append(f"apply:{len(proposals)}"),
    )
    monkeypatch.setattr(
        accepted_pipeline,
        "_persist_junction_state",
        lambda: calls.append("persist") or accepted_pipeline._junction_state_path,
    )
    monkeypatch.setattr(
        accepted_pipeline,
        "run_analysis",
        lambda stage_callback=None: calls.append("rerun") or accepted_pipeline,
    )
    monkeypatch.setattr(
        accepted_pipeline,
        "junction_review_summary",
        lambda: {"round": 2, "selected": 3, "deferred": 1},
    )

    result = accepted_pipeline.complete_junction_round()

    assert calls == ["apply:1", "persist", "rerun"]
    assert accepted_pipeline._rejected_junction_signatures == {"rejected-signature"}
    assert result.accepted_junctions == 1
    assert result.rejected_junctions == 1
    assert result.geometry_changed is True
    assert result.next_junctions == 3

    rejected_pipeline = _ready(_pipeline(tmp_path / "rejected"))
    only_rejected = _proposal()
    only_rejected.decision = "reject"
    rejected_pipeline._junctions = [only_rejected]
    no_geometry_calls: list[str] = []
    monkeypatch.setattr(
        rejected_pipeline,
        "_prepare_junction_round",
        lambda: no_geometry_calls.append("prepare"),
    )
    monkeypatch.setattr(
        rejected_pipeline,
        "_persist_junction_state",
        lambda: no_geometry_calls.append("persist") or rejected_pipeline._junction_state_path,
    )
    monkeypatch.setattr(
        rejected_pipeline,
        "junction_review_summary",
        lambda: {"round": 2, "selected": 0, "deferred": 0},
    )

    result = rejected_pipeline.complete_junction_round()

    assert no_geometry_calls == ["prepare", "persist"]
    assert result.geometry_changed is False
    assert rejected_pipeline.has_unsaved_decisions is False


def test_junction_finalization_writes_geojson_decisions_and_connection_audit(
    tmp_path: Path,
) -> None:
    pipeline = _ready(_pipeline(tmp_path))
    pipeline.namespace.update(
        {
            "SECTION_24_OUTPUT_CSV": pipeline.config.output_dir / "decisions.csv",
            "CONNECTION_AUDIT_CSV": pipeline.config.output_dir / "connections.csv",
        }
    )
    pipeline._accepted_pair_keys = {"1||101"}
    pipeline._working_file_1.write_bytes(pipeline.config.county_file_1.read_bytes())
    pipeline._working_file_2.write_bytes(pipeline.config.county_file_2.read_bytes())
    pipeline._accepted_junction_history = [
        {
            "junction_id": "R1-J0001",
            "round_number": 1,
            "signature": "signature",
            "average_probability": 0.9,
            "junction_x": 500000.0,
            "junction_y": 4400000.0,
            "member_audit": [
                {
                    "county_index": 1,
                    "road_id": "1",
                    "road_name": "Alpha Road",
                    "attachment_type": "START",
                    "part_index": 0,
                    "movement_m": 0.5,
                }
            ],
        }
    ]

    result = pipeline._finalize_junction_outputs()

    assert result.county_1_output.read_bytes() == pipeline.config.county_file_1.read_bytes()
    assert result.county_2_output.read_bytes() == pipeline.config.county_file_2.read_bytes()
    decisions = pd.read_csv(result.final_decisions_csv, dtype=str)
    assert decisions["is_valid"].tolist() == ["yes", "no"]
    assert len(pd.read_csv(result.decision_audit_csv)) == 2
    connection = pd.read_csv(result.connection_audit_csv)
    assert connection.loc[0, "junction_id"] == "R1-J0001"
    assert connection.loc[0, "shared_junction_longitude"] == pytest.approx(-87.0)
    assert result.accepted_pairs == 1
    assert result.rejected_pairs == 1
    assert pipeline._junction_state_path.is_file()


def test_finalize_rejects_pending_or_uncommitted_junction_round(tmp_path: Path) -> None:
    pending_pipeline = _ready(_pipeline(tmp_path / "pending"))
    pending_pipeline._junctions = [_proposal()]
    with pytest.raises(ValueError, match="remain unanswered"):
        pending_pipeline.finalize()

    committed_pipeline = _ready(_pipeline(tmp_path / "committed"))
    reviewed = _proposal()
    reviewed.decision = "reject"
    committed_pipeline._junctions = [reviewed]
    with pytest.raises(ValueError, match="has not been committed"):
        committed_pipeline.finalize()
