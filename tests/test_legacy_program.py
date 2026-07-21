from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from app.core.legacy_program import CELLS

EXPECTED_LEGACY_FILE_SHA256 = "6bd3a86cccdcd84fca77c66271cb85b4a8884d82ad66a4c8685b9ad17ba90467"
EXPECTED_RETAINED_CELLS_SHA256 = "f0b4f3a1a1b26f26336be33c82b727baf10c7a83d75bec07d0c038eb213e01a6"
NOTEBOOK_CODE_CELL_NUMBERS = [15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39]
NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "docs" / (
    "7_Two_GeoJSON_Road_Matching_Automation_2_to_30_percent_"
    "sync_manual_review.ipynb"
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
    assert len(CELLS) == 13
    for filename, source in CELLS:
        ast.parse(source, filename=filename)


def test_retained_algorithm_fingerprint_is_unchanged() -> None:
    legacy_path = Path(__file__).resolve().parents[1] / "app" / "core" / "legacy_program.py"
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == EXPECTED_LEGACY_FILE_SHA256
    assert _legacy_digest() == EXPECTED_RETAINED_CELLS_SHA256


def test_retained_sources_match_embedded_notebook() -> None:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    for position, (cell_number, (_, retained_source)) in enumerate(
        zip(NOTEBOOK_CODE_CELL_NUMBERS, CELLS, strict=True)
    ):
        notebook_source = "".join(notebook["cells"][cell_number - 1]["source"])
        if position == 8:
            # Desktop supplies its own display adapter; this is the only
            # intentional source-line removal from the retained analytical cells.
            notebook_source = notebook_source.replace(
                "from IPython.display import display\n", ""
            )
        assert retained_source == notebook_source
