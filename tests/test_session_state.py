from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.pipeline import PipelineConfig, RoadMatchingPipeline


def _minimal_pipeline(tmp_path: Path) -> RoadMatchingPipeline:
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    first.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    second.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    output = tmp_path / "output"
    pipeline = RoadMatchingPipeline(PipelineConfig(first, second, output))
    pipeline._state_dir = output / ".road_matcher_state"
    pipeline._state_dir.mkdir(parents=True)
    pipeline._pair_run_key = "Alpha__Beta"
    pipeline._session_csv_path = (
        pipeline._state_dir / "Alpha__Beta_manual_review_progress.csv"
    )
    pipeline._build_session_identity()
    pipeline._analysis_complete = True
    pipeline.namespace = {
        "section_24_decision_df": pd.DataFrame(
            [
                {
                    "pair_key": "1||2",
                    "county_1_id": "1",
                    "county_2_id": "2",
                    "manual_pair_number": 1,
                    "manual_batch_id": 1,
                    "requires_manual_verification": True,
                    "manual_decision": pd.NA,
                }
            ]
        )
    }
    return pipeline


def test_decision_is_ram_only_until_explicit_save(tmp_path: Path) -> None:
    pipeline = _minimal_pipeline(tmp_path)
    pipeline.record_manual_decision("1||2", "yes")
    assert pipeline.has_unsaved_decisions
    assert not pipeline.session_path.exists()

    pipeline.save_session(force=True, reason="quit")
    assert pipeline.session_path.exists()
    assert not pipeline.has_unsaved_decisions


def test_save_atomically_replaces_old_session_and_deletes_recovery(tmp_path: Path) -> None:
    pipeline = _minimal_pipeline(tmp_path)
    recovery = pipeline._state_dir / "Alpha__Beta_manual_review_progress_recovery_old.csv"
    recovery.write_text("old", encoding="utf-8")

    pipeline.record_manual_decision("1||2", "yes")
    pipeline.save_session(force=True, reason="first quit")
    first = pd.read_csv(pipeline.session_path)
    assert first.loc[0, "manual_decision"] == "yes"
    assert not recovery.exists()

    pipeline.record_manual_decision("1||2", "no")
    pipeline.save_session(force=True, reason="second quit")
    second = pd.read_csv(pipeline.session_path)
    assert second.loc[0, "manual_decision"] == "no"
    candidates = list(
        pipeline._state_dir.glob("Alpha__Beta_manual_review_progress*.csv")
    )
    assert candidates == [pipeline.session_path]


def test_changed_input_rejects_previous_session(tmp_path: Path) -> None:
    pipeline = _minimal_pipeline(tmp_path)
    pipeline.record_manual_decision("1||2", "yes")
    pipeline.save_session(force=True, reason="quit")

    pipeline.config.county_file_1.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},"geometry":null}]}',
        encoding="utf-8",
    )
    new_pipeline = RoadMatchingPipeline(pipeline.config)
    new_pipeline._state_dir = pipeline._state_dir
    new_pipeline._pair_run_key = "Alpha__Beta"
    new_pipeline._session_csv_path = pipeline.session_path
    new_pipeline._build_session_identity()
    assert new_pipeline._find_session_to_load() is None
