from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.pipeline import RoadMatchingPipeline


def _pipeline_with_decisions(total: int, selected: int) -> RoadMatchingPipeline:
    pipeline = object.__new__(RoadMatchingPipeline)
    pipeline._analysis_complete = True
    pipeline.config = SimpleNamespace(
        min_manual_review_fraction=0.02,
        max_manual_review_fraction=0.30,
    )
    rows = []
    for index in range(total):
        is_selected = index < selected
        # Deliberately reverse probability relative to notebook rank so the
        # desktop-order assertion proves it is independently sorted.
        probability = (total - index) / max(total, 1)
        rows.append(
            {
                "pair_key": f"{index}||{index + 1000}",
                "requires_manual_verification": is_selected,
                "manual_decision": pd.NA,
                "manual_pair_number": index + 1 if is_selected else pd.NA,
                "manual_review_rank": index + 1,
                "probablity": probability,
            }
        )
    pipeline.namespace = {"section_24_decision_df": pd.DataFrame(rows)}
    return pipeline


def test_notebook_review_bounds_match_ceil_2_percent_floor_30_percent() -> None:
    minimum, maximum = RoadMatchingPipeline._manual_review_bounds_for_total(
        2264, 0.02, 0.30
    )
    assert minimum == 46
    assert maximum == 679

    # Tiny-dataset integer-rounding exception is identical to notebook #7.
    assert RoadMatchingPipeline._manual_review_bounds_for_total(1, 0.02, 0.30) == (
        1,
        1,
    )


def test_selected_count_is_strictly_verified_and_review_order_is_ascending() -> None:
    pipeline = _pipeline_with_decisions(total=2264, selected=50)
    diagnostics = pipeline.manual_review_diagnostics()
    assert diagnostics["within_bounds"] is True
    assert diagnostics["minimum"] == 46
    assert diagnostics["maximum"] == 679
    assert diagnostics["selected"] == 50
    assert diagnostics["selected_fraction"] == pytest.approx(50 / 2264)

    probabilities = pd.to_numeric(pipeline.review_rows()["probablity"]).tolist()
    assert probabilities == sorted(probabilities)


def test_out_of_bounds_review_count_fails_loudly() -> None:
    pipeline = _pipeline_with_decisions(total=1000, selected=10)
    with pytest.raises(RuntimeError, match="outside notebook #7"):
        pipeline.manual_review_diagnostics()
