from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pandas as pd

from app.core.legacy_program import CELLS

EXPECTED_LEGACY_FILE_SHA256 = "cb51fff7958439d0ecb50c1e7c91e1507b56de67205aa31d65b75ae779fe9eda"
EXPECTED_RETAINED_CELLS_SHA256 = "b2aec488afa9fd1c37df61f1261b736afbe60da5ca033e358e1ab94080c7ce1c"
EXPECTED_NOTEBOOK_GEOMETRIC_CELL_SHA256 = (
    "b9631799e616f5f15b5cc4f573c04ddc7a08438840748e81935adb9796c8464c"
)


def _legacy_digest() -> str:
    digest = hashlib.sha256()
    for filename, source in CELLS:
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def test_all_retained_notebook_cells_compile() -> None:
    assert len(CELLS) == 12
    for filename, source in CELLS:
        ast.parse(source, filename=filename)


def test_safe_reject_uses_only_the_notebook_global_threshold() -> None:
    cells = dict(CELLS)
    safe_reject_source = cells["notebook_cell_32_safe_reject.py"]

    assert "SAFE17_GLOBAL_THRESHOLD" in safe_reject_source
    assert "safe_reject_mask = base_global_reject" in safe_reject_source
    for desktop_only_marker in (
        "SAFE17_PARALLEL_THRESHOLD",
        "SAFE17_ORTHOGONAL_THRESHOLD",
        "_safe17_local_shape_metrics",
        "safe17_geometry_class",
        "parallel_extra_reject",
        "orthogonal_extra_reject",
    ):
        assert desktop_only_marker not in safe_reject_source


def test_notebook_geometric_probability_model_is_preserved() -> None:
    geometric_source = dict(CELLS)["notebook_cell_26.py"]

    assert hashlib.sha256(geometric_source.encode("utf-8")).hexdigest() == (
        EXPECTED_NOTEBOOK_GEOMETRIC_CELL_SHA256
    )
    assert "SECTION_14_RULE_WEIGHT = 0.70" in geometric_source
    assert "SECTION_14_GMM_WEIGHT = 0.30" in geometric_source
    assert "parallel_continuation_score" in geometric_source
    assert "perpendicular_t_junction_score" in geometric_source
    assert "curved_connection_score" in geometric_source
    assert "parallel_separate_penalty" in geometric_source


def test_safe_reject_runs_without_desktop_only_geometry_inputs() -> None:
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

    source = dict(CELLS)["notebook_cell_32_safe_reject.py"]
    exec(compile(source, "notebook_cell_32_safe_reject.py", "exec"), namespace)

    result = namespace["features_df"]
    expected = result["probablity"].lt(0.5).map(
        {True: "SAFE_REJECT", False: "MANUAL_REVIEW"}
    )
    assert result["safe_reject_decision"].tolist() == expected.tolist()
    assert "safe17_geometry_class" not in result.columns


def test_retained_algorithm_fingerprint_is_unchanged() -> None:
    legacy_path = Path(__file__).resolve().parents[1] / "app" / "core" / "legacy_program.py"
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == EXPECTED_LEGACY_FILE_SHA256
    assert _legacy_digest() == EXPECTED_RETAINED_CELLS_SHA256
