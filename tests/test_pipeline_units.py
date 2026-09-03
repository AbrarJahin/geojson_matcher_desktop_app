from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from shapely.geometry import LineString, Point

import app.core.pipeline as pipeline_module
from app.core.junctions import JunctionMember, JunctionProposal
from app.core.pipeline import PipelineConfig, RoadMatchingPipeline, county_name_from_filename, safe_run_key_part


def _write_geojson(path: Path, *, marker: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "name": marker, "features": []}),
        encoding="utf-8",
    )


def _pipeline(tmp_path: Path) -> RoadMatchingPipeline:
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    _write_geojson(first, marker="alpha")
    _write_geojson(second, marker="beta")
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
                "manual_review_rank": 2,
                "requires_manual_verification": True,
                "manual_decision": pd.NA,
                "safe_reject_decision": "MANUAL_REVIEW",
                "probablity": 0.7,
            },
            {
                "pair_key": "2||102",
                "county_1_id": "2",
                "county_2_id": "102",
                "manual_pair_number": pd.NA,
                "manual_batch_id": pd.NA,
                "manual_review_rank": 1,
                "requires_manual_verification": False,
                "manual_decision": pd.NA,
                "safe_reject_decision": "SAFE_REJECT",
                "probablity": 0.1,
            },
        ]
    )


def _analysis_ready(pipeline: RoadMatchingPipeline) -> RoadMatchingPipeline:
    pipeline._analysis_complete = True
    pipeline._build_session_identity()
    pipeline.namespace = {
        "section_24_decision_df": _decision_frame(),
        "SAFE17_GLOBAL_THRESHOLD": 0.5,
    }
    return pipeline


def test_county_name_and_safe_key_helpers_cover_edge_cases() -> None:
    assert county_name_from_filename("Hamilton_County_Boundary(8).json") == "Hamilton"
    assert county_name_from_filename("Marion_Boundary.geojson") == "Marion"
    with pytest.raises(ValueError, match="readable name"):
        county_name_from_filename("_Boundary.json")
    assert safe_run_key_part(" Lake / County : 01 ") == "Lake_County_01"


def test_config_normalized_orders_files_resolves_paths_and_coerces_values(tmp_path: Path) -> None:
    alpha = tmp_path / "Alpha.geojson"
    beta = tmp_path / "beta.geojson"
    _write_geojson(alpha)
    _write_geojson(beta)
    cfg = PipelineConfig(
        beta,
        alpha,
        tmp_path / "out",
        buffer_distance_meters=12,
        road_id_column=" OBJECTID ",
        max_manual_batch_size=7,
    ).normalized()
    assert cfg.county_file_1 == alpha.resolve()
    assert cfg.county_file_2 == beta.resolve()
    assert cfg.output_dir == (tmp_path / "out").resolve()
    assert cfg.buffer_distance_meters == 12.0
    assert cfg.target_crs == "EPSG:26916"
    assert cfg.road_id_column == "OBJECTID"
    assert cfg.max_manual_batch_size == 7


def test_projected_crs_is_internal_and_cannot_be_overridden(tmp_path: Path) -> None:
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    _write_geojson(first)
    _write_geojson(second)

    with pytest.raises(TypeError, match="target_crs"):
        PipelineConfig(  # type: ignore[call-arg]
            first,
            second,
            tmp_path / "out",
            target_crs="EPSG:4326",
        )


@pytest.mark.parametrize(
    ("mutator", "error_type", "message"),
    [
        (lambda c, p: PipelineConfig(p / "missing.geojson", c.county_file_2, c.output_dir), FileNotFoundError, "does not exist"),
        (lambda c, p: PipelineConfig(c.county_file_1.with_suffix(".txt"), c.county_file_2, c.output_dir), FileNotFoundError, "does not exist"),
        (lambda c, p: PipelineConfig(c.county_file_1, c.county_file_2, c.output_dir, buffer_distance_meters=0), ValueError, "greater than zero"),
        (lambda c, p: PipelineConfig(c.county_file_1, c.county_file_2, c.output_dir, road_id_column=""), ValueError, "cannot be empty"),
        (lambda c, p: PipelineConfig(c.county_file_1, c.county_file_2, c.output_dir, min_manual_review_fraction=0), ValueError, "fractions"),
        (lambda c, p: PipelineConfig(c.county_file_1, c.county_file_2, c.output_dir, min_manual_review_fraction=0.8, max_manual_review_fraction=0.3), ValueError, "fractions"),
        (lambda c, p: PipelineConfig(c.county_file_1, c.county_file_2, c.output_dir, max_manual_batch_size=0), ValueError, "at least 1"),
    ],
)
def test_config_validation_rejects_invalid_settings(tmp_path: Path, mutator, error_type, message) -> None:  # type: ignore[no-untyped-def]
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    _write_geojson(first)
    _write_geojson(second)
    base = PipelineConfig(first, second, tmp_path / "out")
    config = mutator(base, tmp_path)
    with pytest.raises(error_type, match=message):
        config.validate()


def test_config_validation_rejects_existing_non_geojson_input(tmp_path: Path) -> None:
    first = tmp_path / "Alpha.txt"
    second = tmp_path / "Beta.geojson"
    first.write_text("x", encoding="utf-8")
    _write_geojson(second)
    with pytest.raises(ValueError, match=".json or .geojson"):
        PipelineConfig(first, second, tmp_path / "out").validate()


def test_directory_writable_probe_creates_directory_and_cleans_probe(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out"
    RoadMatchingPipeline._assert_directory_writable(target, "Output folder")
    assert target.is_dir()
    assert list(target.glob(".road_matcher_write_test_*")) == []


def test_directory_writable_probe_translates_os_error(tmp_path: Path, monkeypatch) -> None:
    original = Path.write_text

    def fail_probe(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name.startswith(".road_matcher_write_test_"):
            raise OSError("read only")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_probe)
    with pytest.raises(PermissionError, match="is not writable"):
        RoadMatchingPipeline._assert_directory_writable(tmp_path / "out", "Output folder")


def test_analysis_input_files_switches_to_working_files_after_accepted_junction(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    assert pipeline._analysis_input_files() == (
        pipeline.config.county_file_1,
        pipeline.config.county_file_2,
    )

    pipeline._accepted_junction_history = [{"junction_id": "J1"}]
    with pytest.raises(RuntimeError, match="working GeoJSON files"):
        pipeline._analysis_input_files()

    _write_geojson(pipeline._working_file_1)
    _write_geojson(pipeline._working_file_2)
    assert pipeline._analysis_input_files() == (
        pipeline._working_file_1,
        pipeline._working_file_2,
    )


def test_logger_print_and_display_helpers_are_safe(tmp_path: Path) -> None:
    messages: list[str] = []
    pipeline = _pipeline(tmp_path)
    pipeline.set_logger(messages.append)
    pipeline.log("hello\n")
    pipeline._print("a", "b", sep="|", end="!")
    frame = pd.DataFrame({"x": [1, 2]})
    assert pipeline._display(frame) is frame
    assert pipeline._display("value") == "value"
    assert messages == ["hello", "a|b!", "DataFrame: 2 rows × 1 columns", "value"]


def test_require_analysis_and_properties_fail_before_analysis(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    with pytest.raises(RuntimeError, match="Run analysis"):
        _ = pipeline.decision_df
    with pytest.raises(RuntimeError, match="Run analysis"):
        _ = pipeline.features_df
    assert pipeline.has_unsaved_decisions is False


def test_analysis_properties_review_rows_and_summary(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    assert pipeline.county_1_name == "Alpha"
    assert pipeline.county_2_name == "Beta"
    assert pipeline.optimized_threshold == 0.5
    assert pipeline.safe_reject_thresholds == {
        "global": 0.5,
    }
    assert pipeline.review_rows()["pair_key"].tolist() == ["1||101"]
    summary = pipeline.review_summary()
    assert summary["total_candidates"] == 2
    assert summary["safe_rejected"] == 1
    assert summary["selected"] == 1
    assert summary["remaining"] == 1


def test_manual_review_bounds_reject_negative_total() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        RoadMatchingPipeline._manual_review_bounds_for_total(-1, 0.02, 0.30)


def test_manual_review_diagnostics_requires_category_column(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    pipeline.namespace["section_24_decision_df"] = _decision_frame().drop(
        columns=["safe_reject_decision"]
    )
    with pytest.raises(RuntimeError, match="decision column is missing"):
        pipeline.manual_review_diagnostics()


def test_pair_features_returns_highest_probability_duplicate_and_missing_raises(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    pipeline.namespace["_normalize_id"] = lambda value: str(value).replace(".0", "")
    pipeline.namespace["features_df"] = pd.DataFrame(
        [
            {"county_1_road_id": "1", "county_2_road_id": "101", "probablity": 0.2, "tag": "low"},
            {"county_1_road_id": 1.0, "county_2_road_id": 101.0, "probablity": 0.9, "tag": "high"},
        ]
    )
    assert pipeline.pair_features("1", "101")["tag"] == "high"
    with pytest.raises(KeyError, match="Pair not found"):
        pipeline.pair_features("9", "999")


@pytest.mark.parametrize("decision", ["maybe", "", "YES!", "accept"])
def test_record_manual_decision_rejects_invalid_value(tmp_path: Path, decision: str) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    with pytest.raises(ValueError, match="must be 'yes' or 'no'"):
        pipeline.record_manual_decision("1||101", decision)


def test_record_manual_decision_rejects_missing_or_non_review_pair(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    with pytest.raises(KeyError, match="found 0"):
        pipeline.record_manual_decision("missing", "yes")
    with pytest.raises(ValueError, match="not part of the manual-review queue"):
        pipeline.record_manual_decision("2||102", "no")


def test_manual_progress_frame_contains_identity_metadata(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    frame = pipeline._manual_progress_frame()
    assert frame["pair_key"].tolist() == ["1||101"]
    assert frame.loc[frame.index[0], "source_file_1"] == str(pipeline.config.county_file_1)
    assert frame.loc[frame.index[0], "session_signature"] == pipeline._session_signature
    assert frame.loc[frame.index[0], "session_schema_version"] == pipeline_module.SESSION_SCHEMA_VERSION


def test_atomic_replace_csv_replaces_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "state.csv"
    target.write_text("old\n", encoding="utf-8")
    frame = pd.DataFrame({"a": [1], "b": ["x"]})
    RoadMatchingPipeline._atomic_replace_csv(frame, target)
    loaded = pd.read_csv(target)
    assert loaded.to_dict("records") == [{"a": 1, "b": "x"}]
    assert list(tmp_path.glob(".state.csv.*.tmp")) == []


def test_progress_file_compatibility_accepts_valid_and_rejects_invalid(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    progress = pipeline._manual_progress_frame()
    valid = tmp_path / "valid.csv"
    progress.to_csv(valid, index=False)
    assert pipeline._progress_file_is_compatible(valid) is True

    invalid_decision = progress.copy()
    invalid_decision["manual_decision"] = "maybe"
    invalid_path = tmp_path / "bad.csv"
    invalid_decision.to_csv(invalid_path, index=False)
    assert pipeline._progress_file_is_compatible(invalid_path) is False

    missing_columns = tmp_path / "missing-columns.csv"
    pd.DataFrame({"pair_key": ["1"]}).to_csv(missing_columns, index=False)
    assert pipeline._progress_file_is_compatible(missing_columns) is False

    empty = tmp_path / "empty.csv"
    pd.DataFrame(columns=progress.columns).to_csv(empty, index=False)
    assert pipeline._progress_file_is_compatible(empty) is False


def test_progress_file_rejects_signature_or_source_mismatch(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    progress = pipeline._manual_progress_frame()

    changed_signature = progress.copy()
    changed_signature["session_signature"] = "different"
    path = tmp_path / "signature.csv"
    changed_signature.to_csv(path, index=False)
    assert pipeline._progress_file_is_compatible(path) is False

    changed_source = progress.copy()
    changed_source["source_file_1"] = str(tmp_path / "Other.geojson")
    path = tmp_path / "source.csv"
    changed_source.to_csv(path, index=False)
    assert pipeline._progress_file_is_compatible(path) is False


def test_find_session_to_load_chooses_newest_compatible_file(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    pipeline._state_dir.mkdir(parents=True, exist_ok=True)
    frame = pipeline._manual_progress_frame()
    first = pipeline._state_dir / f"{pipeline._pair_run_key}_manual_review_progress_old.csv"
    second = pipeline._state_dir / f"{pipeline._pair_run_key}_manual_review_progress_new.csv"
    frame.to_csv(first, index=False)
    time.sleep(0.01)
    frame.to_csv(second, index=False)
    assert pipeline._find_session_to_load() == second


def test_save_session_short_circuits_before_analysis_and_when_clean(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    assert pipeline.save_session() is None

    pipeline = _analysis_ready(pipeline)
    pipeline._state_dir.mkdir(parents=True, exist_ok=True)
    pipeline._session_csv_path.write_text("existing", encoding="utf-8")
    pipeline._session_dirty = False
    assert pipeline.save_session(force=False) == pipeline._session_csv_path


def test_raw_geojson_feature_index_prefers_property_then_feature_id() -> None:
    payload = {
        "features": [
            {"id": "fallback", "properties": {"OBJECTID": 1}},
            {"id": 2, "properties": {}},
        ]
    }
    index = RoadMatchingPipeline._raw_geojson_feature_index(
        payload, "OBJECTID", lambda value: str(value)
    )
    assert index == {"1": 0, "2": 1}


def test_write_json_temp_is_valid_json_and_does_not_replace_target(tmp_path: Path) -> None:
    target = tmp_path / "working.geojson"
    target.write_text("original", encoding="utf-8")
    temp = RoadMatchingPipeline._write_json_temp({"hello": "world"}, target)
    assert target.read_text(encoding="utf-8") == "original"
    assert json.loads(temp.read_text(encoding="utf-8")) == {"hello": "world"}
    temp.unlink()


def _proposal() -> JunctionProposal:
    members = [
        JunctionMember("1|1", 1, "1", "A", "START", 0, 0, 0, 0),
        JunctionMember("2|2", 2, "2", "B", "START", 0, 0, 0.5, 0),
    ]
    return JunctionProposal(
        junction_id="R1-J0001",
        signature="sig",
        round_number=1,
        seed_pair_key="1||2",
        average_probability=0.8,
        pair_keys=["1||2"],
        members=members,
        suggested_x=0.25,
        suggested_y=0,
        junction_x=0.25,
        junction_y=0,
    )


def test_record_junction_decision_validates_decision_members_and_distance(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    pipeline._junctions = [_proposal()]

    with pytest.raises(ValueError, match="must be 'accept' or 'reject'"):
        pipeline.record_junction_decision("sig", "yes")
    with pytest.raises(KeyError, match="not found"):
        pipeline.record_junction_decision("missing", "reject")
    with pytest.raises(ValueError, match="outside this junction"):
        pipeline.record_junction_decision("sig", "reject", selected_member_keys=["9|9"])
    with pytest.raises(ValueError, match="at least two roads"):
        pipeline.record_junction_decision("sig", "accept", selected_member_keys=["1|1"])

    pipeline._junctions = [_proposal()]
    with pytest.raises(ValueError, match="beyond the configured"):
        pipeline.record_junction_decision("sig", "accept", junction_x=1000, junction_y=1000)


def test_record_junction_decision_accept_and_reject_update_ram_state(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    proposal = _proposal()
    pipeline._junctions = [proposal]
    pipeline.record_junction_decision("sig", "accept", junction_x=0.2, junction_y=0.1)
    assert proposal.decision == "accept"
    assert pipeline._session_dirty is True
    assert pipeline._junction_drafts["sig"]["decision"] == "accept"

    pipeline._junctions = [_proposal()]
    pipeline.record_junction_decision("sig", "reject")
    assert pipeline._junctions[0].decision == "reject"


def test_junction_review_items_and_summary(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    accepted = _proposal()
    accepted.decision = "accept"
    pending = _proposal()
    pending.signature = "sig2"
    pipeline._junctions = [accepted, pending]
    pipeline._junction_round = 3
    pipeline._deferred_junction_count = 4
    pipeline._accepted_junction_history = [{"junction_id": "old"}]
    pipeline._rejected_junction_signatures = {"rejected"}

    assert pipeline.junction_review_items(include_completed=False) == [pending]
    summary = pipeline.junction_review_summary()
    assert summary == {
        "round": 3,
        "selected": 2,
        "completed": 1,
        "remaining": 1,
        "deferred": 4,
        "manual_pairs": 1,
        "accepted_history": 1,
        "rejected_history": 1,
    }


def test_junction_member_geometry_selects_correct_county_and_validates_uniqueness(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    normalize = lambda value: str(value).replace(".0", "")
    pipeline.namespace["_normalize_id"] = normalize
    line_a = LineString([(0, 0), (1, 0)])
    line_b = LineString([(0, 0), (0, 1)])
    import geopandas as gpd

    pipeline.namespace["marion_gdf"] = gpd.GeoDataFrame({"OBJECTID": [1], "geometry": [line_a]}, geometry="geometry")
    pipeline.namespace["hamilton_gdf"] = gpd.GeoDataFrame({"OBJECTID": [2], "geometry": [line_b]}, geometry="geometry")
    member = SimpleNamespace(county_index=2, road_id="2", key="2|2")
    assert pipeline.junction_member_geometry(member).equals(line_b)

    member.road_id = "missing"
    with pytest.raises(KeyError, match="found 0"):
        pipeline.junction_member_geometry(member)


def test_context_layers_prefers_match_layers(tmp_path: Path) -> None:
    pipeline = _analysis_ready(_pipeline(tmp_path))
    pipeline.namespace.update(
        {
            "marion_gdf": "raw-a",
            "hamilton_gdf": "raw-b",
            "marion_match": "match-a",
            "hamilton_match": "match-b",
        }
    )
    assert pipeline.context_layers() == ("match-a", "match-b")
