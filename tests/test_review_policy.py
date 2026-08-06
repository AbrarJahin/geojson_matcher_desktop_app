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

        # Deliberately reverse probability relative to the source rank so the
        # desktop-order assertion proves that review_rows() sorts independently.
        probability = (total - index) / max(total, 1)

        rows.append(
            {
                "pair_key": f"{index}||{index + 1000}",
                "safe_reject_decision": (
                    "MANUAL_REVIEW" if is_selected else "SAFE_REJECT"
                ),
                "requires_manual_verification": is_selected,
                "manual_decision": pd.NA,
                "manual_pair_number": index + 1 if is_selected else pd.NA,
                "manual_review_rank": index + 1,
                "probablity": probability,
            }
        )

    pipeline.namespace = {"section_24_decision_df": pd.DataFrame(rows)}
    return pipeline


def test_two_category_workflow_allows_the_complete_review_range() -> None:
    # The current workflow does not sample a fixed percentage. The actual
    # review count is determined by the SAFE_REJECT/MANUAL_REVIEW partition.
    assert RoadMatchingPipeline._manual_review_bounds_for_total(
        2264, 0.02, 0.30
    ) == (0, 2264)

    assert RoadMatchingPipeline._manual_review_bounds_for_total(
        1, 0.02, 0.30
    ) == (0, 1)

    assert RoadMatchingPipeline._manual_review_bounds_for_total(
        0, 0.02, 0.30
    ) == (0, 0)


def test_partition_is_verified_and_review_order_is_ascending() -> None:
    pipeline = _pipeline_with_decisions(total=2264, selected=50)
    diagnostics = pipeline.manual_review_diagnostics()

    assert diagnostics["within_bounds"] is True
    assert diagnostics["minimum"] == 50
    assert diagnostics["maximum"] == 50
    assert diagnostics["selected"] == 50
    assert diagnostics["safe_rejected"] == 2214
    assert diagnostics["selected_fraction"] == pytest.approx(50 / 2264)
    assert diagnostics["safe_reject_fraction"] == pytest.approx(2214 / 2264)

    probabilities = pd.to_numeric(pipeline.review_rows()["probablity"]).tolist()
    assert probabilities == sorted(probabilities)


def test_invalid_two_category_partition_fails_loudly() -> None:
    pipeline = _pipeline_with_decisions(total=1000, selected=10)
    frame = pipeline.namespace["section_24_decision_df"]

    # Deliberately create a contradiction: the pair remains queued for manual
    # verification but is categorized as SAFE_REJECT.
    frame.loc[0, "safe_reject_decision"] = "SAFE_REJECT"

    with pytest.raises(RuntimeError, match="two-category partition is invalid"):
        pipeline.manual_review_diagnostics()
