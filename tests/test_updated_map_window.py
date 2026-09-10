from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from shapely.geometry import LineString, Point

from app.core.pipeline import FinalizationResult
import app.ui.updated_map_window as updated_map_module
from app.ui.updated_map_window import (
    REVIEW_BUTTON_TEXT,
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


def _pipeline() -> SimpleNamespace:
    return SimpleNamespace(
        county_1_name="Alpha",
        county_2_name="Beta",
        log=lambda _message: None,
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


def test_analysis_completion_resets_updated_map_mode_before_base_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    window = MainWindow()
    window._updated_map_result = _result(tmp_path)
    window.review_button.setText(UPDATED_MAP_BUTTON_TEXT)
    observed: list[object] = []

    monkeypatch.setattr(
        updated_map_module.BaseMainWindow,
        "_analysis_completed",
        lambda self, pipeline: observed.append(
            (pipeline, self._updated_map_result, self.review_button.text())
        ),
    )
    pipeline = SimpleNamespace()

    window._analysis_completed(pipeline)  # type: ignore[arg-type]

    assert observed == [(pipeline, None, REVIEW_BUTTON_TEXT)]
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


def test_open_updated_map_returns_safely_when_result_or_pipeline_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    window = MainWindow()
    created: list[object] = []
    monkeypatch.setattr(
        updated_map_module,
        "UpdatedMapDialog",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )

    window._open_updated_map()
    window._updated_map_result = _result(tmp_path)
    window._open_updated_map()

    assert created == []
    window.pipeline = None
    window.close()


def test_open_updated_map_reports_dialog_creation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    window = MainWindow()
    window._updated_map_result = _result(tmp_path)
    window.pipeline = SimpleNamespace()  # type: ignore[assignment]
    errors: list[tuple[str, str]] = []

    class BrokenDialog:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("viewer boom")

    monkeypatch.setattr(updated_map_module, "UpdatedMapDialog", BrokenDialog)
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, text: errors.append((str(title), str(text))),
    )

    window._open_updated_map()

    assert errors
    assert errors[0][0] == "Could not open updated map"
    assert "viewer boom" in errors[0][1]
    window.pipeline = None
    window.close()


def test_updated_map_dialog_uses_distinct_layers_and_toggle_controls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    result = _result(tmp_path)
    pipeline = _pipeline()

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


def test_load_output_layer_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Updated GeoJSON file was not found"):
        UpdatedMapDialog._load_output_layer(tmp_path / "missing.geojson")


def test_load_output_layer_assigns_wgs84_filters_non_lines_and_projects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "mixed.geojson"
    source.write_text("{}", encoding="utf-8")
    frame = gpd.GeoDataFrame(
        {
            "kind": ["road", "point"],
            "geometry": [
                LineString([(-86.15, 39.92), (-86.14, 39.92)]),
                Point(-86.15, 39.92),
            ],
        },
        geometry="geometry",
        crs=None,
    )
    monkeypatch.setattr(
        updated_map_module.gpd,
        "read_file",
        lambda *args, **kwargs: frame.copy(),
    )

    loaded = UpdatedMapDialog._load_output_layer(source)

    assert len(loaded) == 1
    assert loaded.iloc[0]["kind"] == "road"
    assert loaded.geometry.iloc[0].geom_type == "LineString"
    assert loaded.crs is not None
    assert loaded.crs.to_string() == "EPSG:3857"


def test_load_output_layer_rejects_file_without_drawable_roads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "points.geojson"
    source.write_text("{}", encoding="utf-8")
    frame = gpd.GeoDataFrame(
        {"geometry": [Point(-86.15, 39.92)]},
        geometry="geometry",
        crs="EPSG:4326",
    )
    monkeypatch.setattr(
        updated_map_module.gpd,
        "read_file",
        lambda *args, **kwargs: frame.copy(),
    )

    with pytest.raises(ValueError, match="No drawable road geometry"):
        UpdatedMapDialog._load_output_layer(source)


def test_combined_bounds_cover_both_layers_and_add_padding() -> None:
    first = SimpleNamespace(total_bounds=(0.0, 0.0, 10.0, 10.0))
    second = SimpleNamespace(total_bounds=(-5.0, 2.0, 20.0, 8.0))

    west, south, east, north = UpdatedMapDialog._combined_bounds(
        first,  # type: ignore[arg-type]
        second,  # type: ignore[arg-type]
    )

    assert west < -5.0
    assert south < 0.0
    assert east > 20.0
    assert north > 10.0


@pytest.mark.parametrize(
    "bad_bounds",
    [
        (float("nan"), 0.0, 1.0, 1.0),
        (0.0, float("inf"), 1.0, 1.0),
        (0.0, 0.0, float("-inf"), 1.0),
    ],
)
def test_combined_bounds_reject_nonfinite_coordinates(bad_bounds) -> None:  # type: ignore[no-untyped-def]
    first = SimpleNamespace(total_bounds=bad_bounds)
    second = SimpleNamespace(total_bounds=(0.0, 0.0, 1.0, 1.0))

    with pytest.raises(ValueError, match="non-finite"):
        UpdatedMapDialog._combined_bounds(
            first,  # type: ignore[arg-type]
            second,  # type: ignore[arg-type]
        )


def test_basemap_enable_with_invalid_view_does_not_schedule_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    dialog = UpdatedMapDialog(
        _pipeline(),  # type: ignore[arg-type]
        _result(tmp_path),
        include_basemap=False,
    )
    scheduled: list[object] = []
    monkeypatch.setattr(dialog.canvas, "_view_bounds", lambda: None)
    monkeypatch.setattr(
        dialog.canvas,
        "_schedule_basemap",
        lambda *args: scheduled.append(args),
    )

    dialog.basemap_button.setChecked(True)
    app.processEvents()

    assert scheduled == []
    assert dialog.canvas._basemap_enabled is True
    dialog.close()
    app.processEvents()


def test_basemap_toggle_is_noop_during_canvas_shutdown(
    tmp_path: Path,
) -> None:
    app = _app()
    dialog = UpdatedMapDialog(
        _pipeline(),  # type: ignore[arg-type]
        _result(tmp_path),
        include_basemap=False,
    )
    dialog.canvas._shutting_down = True

    dialog._set_basemap_visible(True)

    assert dialog.canvas._basemap_enabled is False
    dialog.close()
    app.processEvents()


def test_done_shuts_down_canvas_only_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    dialog = UpdatedMapDialog(
        _pipeline(),  # type: ignore[arg-type]
        _result(tmp_path),
        include_basemap=False,
    )
    calls: list[str] = []
    monkeypatch.setattr(dialog.canvas, "shutdown", lambda: calls.append("shutdown"))

    dialog.done(0)
    dialog.done(0)

    assert calls == ["shutdown"]
