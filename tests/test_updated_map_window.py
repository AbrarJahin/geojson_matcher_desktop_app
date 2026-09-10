from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.pipeline import FinalizationResult
import app.ui.updated_map_window as updated_map_module
from app.ui.updated_map_window import (
    UPDATED_MAP_BUTTON_TEXT,
    MainWindow,
    UpdatedMapDialog,
)


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_geojson(path: Path, road_id: int, coordinates: list[list[float]]) -> None:
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
                            "coordinates": coordinates,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _result(tmp_path: Path) -> FinalizationResult:
    first = tmp_path / "Alpha_updated.json"
    second = tmp_path / "Beta_updated.json"
    _write_geojson(
        first,
        1,
        [[-86.1459, 39.9271], [-86.1450, 39.9271]],
    )
    _write_geojson(
        second,
        101,
        [[-86.1459, 39.9262], [-86.1459, 39.9271]],
    )
    return FinalizationResult(
        county_1_output=first,
        county_2_output=second,
        final_decisions_csv=tmp_path / "decisions.csv",
        decision_audit_csv=tmp_path / "audit.csv",
        connection_audit_csv=tmp_path / "connections.csv",
        accepted_pairs=1,
        rejected_pairs=0,
    )


def test_finalization_switches_review_button_only_after_completion_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    window = MainWindow()
    result = _result(tmp_path)
    calls: list[str] = []

    def information(_parent, title, _text):  # type: ignore[no-untyped-def]
        calls.append(str(title))
        assert window.review_button.text() != UPDATED_MAP_BUTTON_TEXT
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", information)

    window._finalization_completed(result)

    assert calls == ["Road matching complete"]
    assert window.review_button.text() == UPDATED_MAP_BUTTON_TEXT
    assert window.review_button.isEnabled() is True
    assert window._updated_map_result == result
    window.pipeline = None
    window.close()


def test_open_review_routes_to_updated_map_after_finalization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    window = MainWindow()
    result = _result(tmp_path)
    window._updated_map_result = result
    window.pipeline = SimpleNamespace()  # type: ignore[assignment]
    opened: list[tuple[object, FinalizationResult, bool]] = []

    class FakeUpdatedMapDialog:
        def __init__(
            self,
            pipeline,
            received_result,
            *,
            include_basemap,
            parent,
        ):  # type: ignore[no-untyped-def]
            assert parent is window
            opened.append((pipeline, received_result, include_basemap))

        def exec(self) -> int:
            return 1

    monkeypatch.setattr(updated_map_module, "UpdatedMapDialog", FakeUpdatedMapDialog)
    window.basemap_checkbox.setChecked(True)

    window._open_review()

    assert len(opened) == 1
    assert opened[0][1] == result
    assert opened[0][2] is True
    window.pipeline = None
    window.close()


def test_updated_map_dialog_uses_distinct_layers_and_toggle_controls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    result = _result(tmp_path)
    pipeline = SimpleNamespace(
        county_1_name="Alpha",
        county_2_name="Beta",
        log=lambda _message: None,
    )

    dialog = UpdatedMapDialog(
        pipeline,  # type: ignore[arg-type]
        result,
        include_basemap=False,
    )
    app.processEvents()

    lines = dialog.canvas.axes.get_lines()
    assert len(lines) == 2
    assert lines[0].get_color() != lines[1].get_color()
    assert dialog.toolbar.drag_junction_button.isHidden() is True

    dialog.axes_button.setChecked(False)
    app.processEvents()
    assert dialog.canvas.axes.axison is False

    dialog.axes_button.setChecked(True)
    app.processEvents()
    assert dialog.canvas.axes.axison is True

    scheduled: list[tuple[int, tuple[float, float, float, float], object]] = []
    monkeypatch.setattr(
        dialog.canvas,
        "_schedule_basemap",
        lambda token, bounds, active_pipeline: scheduled.append(
            (token, bounds, active_pipeline)
        ),
    )
    dialog.basemap_button.setChecked(True)
    app.processEvents()

    assert len(scheduled) == 1
    assert scheduled[0][2] is pipeline

    dialog.basemap_button.setChecked(False)
    app.processEvents()
    assert dialog.canvas._basemap_enabled is False
    dialog.close()
    app.processEvents()
