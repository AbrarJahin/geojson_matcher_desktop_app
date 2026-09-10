from __future__ import annotations

import ast

import pandas as pd
import pytest

import app.core.legacy_program as legacy_program
from app.core.legacy_program import CELLS, execute_legacy_pipeline


def _safe_reject_source() -> str:
    return dict(CELLS)["notebook_cell_32_safe_reject.py"]


def test_all_retained_notebook_cells_have_unique_names_and_compile() -> None:
    assert len(CELLS) == 12
    filenames = [filename for filename, _source in CELLS]
    assert len(filenames) == len(set(filenames))
    for filename, source in CELLS:
        ast.parse(source, filename=filename)


def test_geometric_probability_contract_is_preserved() -> None:
    source = dict(CELLS)["notebook_cell_26.py"]

    assert "SECTION_14_RULE_WEIGHT = 0.70" in source
    assert "SECTION_14_GMM_WEIGHT = 0.30" in source
    assert "parallel_continuation_score" in source
    assert "perpendicular_t_junction_score" in source
    assert "curved_connection_score" in source
    assert "parallel_separate_penalty" in source
    assert 'features_df["geometric_rule_probability"]' in source


def test_safe_reject_contract_has_only_two_operational_categories() -> None:
    source = _safe_reject_source()

    assert 'SAFE17_SAFE_REJECT = "SAFE_REJECT"' in source
    assert 'SAFE17_MANUAL_REVIEW = "MANUAL_REVIEW"' in source
    assert "SAFE17_GLOBAL_THRESHOLD" in source
    assert (
        "safe_reject_mask = base_global_reject & ~safe17_strong_direct_agreement"
        in source
    )
    assert "SAFE17_DIRECT_GEOMETRY_THRESHOLD = 0.75" in source
    assert "SAFE17_DIRECT_TEXTUAL_THRESHOLD = 0.75" in source
    assert "SAFE17_DIRECT_MAX_DISAGREEMENT = 0.10" in source

    # These desktop-only shortcuts must not silently become analytical rules.
    for forbidden in (
        "SAFE17_PARALLEL_THRESHOLD",
        "SAFE17_ORTHOGONAL_THRESHOLD",
        "_safe17_local_shape_metrics",
        "safe17_geometry_class",
        "parallel_extra_reject",
        "orthogonal_extra_reject",
    ):
        assert forbidden not in source


def test_safe_reject_executes_and_never_auto_accepts() -> None:
    namespace = {
        "features_df": pd.DataFrame(
            {
                "geometric_valid_pair_probability": [0.1, 0.8],
                "textual_valid_pair_probability": [0.1, 0.8],
                "county_1_road_id": [1, 2],
                "county_2_road_id": [101, 102],
            }
        ),
        "SAFE_REJECT_GLOBAL_THRESHOLD": 0.5,
        "display": lambda value: value,
    }

    source = _safe_reject_source()
    exec(compile(source, "notebook_cell_32_safe_reject.py", "exec"), namespace)

    result = namespace["features_df"]
    assert set(result["safe_reject_decision"]) <= {
        "SAFE_REJECT",
        "MANUAL_REVIEW",
    }
    assert result["safe_reject_decision"].tolist() == [
        "SAFE_REJECT",
        "MANUAL_REVIEW",
    ]
    assert result.loc[0, "safe17_is_automatic_reject"] == 1
    assert result.loc[1, "safe17_is_automatic_reject"] == 0
    assert "safe17_geometry_class" not in result.columns


@pytest.mark.parametrize(
    ("namespace", "message"),
    [
        (
            {"SAFE_REJECT_GLOBAL_THRESHOLD": 0.5},
            "features_df was not found",
        ),
        (
            {
                "features_df": pd.DataFrame(
                    {
                        "geometric_valid_pair_probability": [0.5],
                        "textual_valid_pair_probability": [0.5],
                        "county_1_road_id": [1],
                        "county_2_road_id": [2],
                    }
                ),
                "SAFE_REJECT_GLOBAL_THRESHOLD": 2.0,
            },
            "outside",
        ),
        (
            {
                "features_df": pd.DataFrame(
                    {
                        "geometric_valid_pair_probability": [0.5],
                        "textual_valid_pair_probability": [0.5],
                    }
                ),
                "SAFE_REJECT_GLOBAL_THRESHOLD": 0.5,
            },
            "missing columns",
        ),
        (
            {
                "features_df": pd.DataFrame(
                    columns=[
                        "geometric_valid_pair_probability",
                        "textual_valid_pair_probability",
                        "county_1_road_id",
                        "county_2_road_id",
                    ]
                ),
                "SAFE_REJECT_GLOBAL_THRESHOLD": 0.5,
            },
            "empty",
        ),
    ],
)
def test_safe_reject_rejects_invalid_inputs(namespace, message) -> None:  # type: ignore[no-untyped-def]
    namespace["display"] = lambda value: value
    with pytest.raises(ValueError, match=message):
        exec(
            compile(
                _safe_reject_source(),
                "notebook_cell_32_safe_reject.py",
                "exec",
            ),
            namespace,
        )


def test_execute_legacy_pipeline_runs_cells_in_order_and_reports_stages(
    monkeypatch,
) -> None:
    cells = (
        ("stage_a.py", "value = 2"),
        ("stage_b.py", "value *= 5"),
        ("stage_c.py", "result = value + 1"),
    )
    monkeypatch.setattr(legacy_program, "CELLS", cells)

    stages: list[tuple[int, int, str]] = []
    namespace: dict[str, object] = {}
    returned = execute_legacy_pipeline(
        namespace,
        stage_callback=lambda position, total, filename: stages.append(
            (position, total, filename)
        ),
    )

    assert returned is namespace
    assert namespace["result"] == 11
    assert stages == [
        (1, 3, "stage_a.py"),
        (2, 3, "stage_b.py"),
        (3, 3, "stage_c.py"),
    ]


def test_execute_legacy_pipeline_can_run_without_stage_callback(monkeypatch) -> None:
    monkeypatch.setattr(
        legacy_program,
        "CELLS",
        (("only.py", "answer = 42"),),
    )
    namespace: dict[str, object] = {}
    execute_legacy_pipeline(namespace)
    assert namespace["answer"] == 42
