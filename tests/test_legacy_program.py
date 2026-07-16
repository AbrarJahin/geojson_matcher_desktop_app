import ast

from app.core.legacy_program import CELLS


def test_all_retained_notebook_cells_compile() -> None:
    assert len(CELLS) == 13
    for filename, source in CELLS:
        ast.parse(source, filename=filename)
