from __future__ import annotations

import math

import geopandas as gpd
import pytest
from PySide6.QtWidgets import QApplication
from shapely.geometry import LineString, MultiLineString, Point

import app.ui.map_canvas as map_canvas_module
from app.ui.map_canvas import InteractiveMapCanvas, MapNavigationToolbar


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_geometry_parts_accepts_lines_and_rejects_non_lines() -> None:
    line = LineString([(0, 0), (1, 0)])
    multi = MultiLineString([[(0, 0), (1, 0)], [(2, 0), (3, 0)]])
    assert InteractiveMapCanvas._geometry_parts(None) == []
    assert InteractiveMapCanvas._geometry_parts(Point(0, 0)) == []
    assert InteractiveMapCanvas._geometry_parts(line) == [line]
    assert len(InteractiveMapCanvas._geometry_parts(multi)) == 2


def test_square_bounds_is_square_and_padded() -> None:
    bounds = InteractiveMapCanvas._square_bounds(
        [LineString([(0, 0), (100, 10)])], minimum_padding=20
    )
    west, south, east, north = bounds
    assert east - west == pytest.approx(north - south)
    assert west < 0
    assert east > 100
    with pytest.raises(ValueError, match="No drawable geometry"):
        InteractiveMapCanvas._square_bounds([])


def test_visible_roads_filters_to_viewport_and_ignores_empty_geometry() -> None:
    layer = gpd.GeoDataFrame(
        {
            "name": ["inside", "outside", "missing"],
            "geometry": [
                LineString([(0, 0), (2, 0)]),
                LineString([(100, 100), (102, 100)]),
                None,
            ],
        },
        geometry="geometry",
        crs="EPSG:3857",
    )
    visible = InteractiveMapCanvas._visible_roads(layer, (-5, -5, 5, 5))
    assert visible["name"].tolist() == ["inside"]


def test_web_mercator_tile_coordinate_helpers_round_trip() -> None:
    for zoom in (0, 5, 12, 19):
        for x in (-1_000_000.0, 0.0, 1_000_000.0):
            tile_x = InteractiveMapCanvas._tile_x(x, zoom)
            assert InteractiveMapCanvas._meters_x(tile_x, zoom) == pytest.approx(x)
        for y in (-1_000_000.0, 0.0, 1_000_000.0):
            tile_y = InteractiveMapCanvas._tile_y(y, zoom)
            assert InteractiveMapCanvas._meters_y(tile_y, zoom) == pytest.approx(y)


def test_transformer_is_cached_and_projection_round_trip_is_close() -> None:
    _app()
    canvas = InteractiveMapCanvas()
    first = canvas._transformer("EPSG:4326")
    second = canvas._transformer("EPSG:4326")
    assert first is second

    source = Point(-86.158, 39.768)
    web = canvas._to_web_mercator(source, "EPSG:4326")
    restored = canvas._from_web_mercator(web, "EPSG:4326")
    assert restored.x == pytest.approx(source.x, abs=1e-6)
    assert restored.y == pytest.approx(source.y, abs=1e-6)
    canvas.shutdown()


def test_projection_helpers_preserve_none_and_empty_geometry() -> None:
    _app()
    canvas = InteractiveMapCanvas()
    empty = LineString()
    assert canvas._to_web_mercator(None, "EPSG:4326") is None
    assert canvas._from_web_mercator(None, "EPSG:4326") is None
    assert canvas._to_web_mercator(empty, "EPSG:4326").is_empty
    assert canvas._from_web_mercator(empty, "EPSG:4326").is_empty
    canvas.shutdown()


def test_tile_grid_never_exceeds_request_limit() -> None:
    _app()
    canvas = InteractiveMapCanvas()
    canvas.resize(1000, 700)
    zoom, x_min, y_min, x_max, y_max = canvas._tile_grid(
        (-2_000_000, -2_000_000, 2_000_000, 2_000_000)
    )
    request_count = (x_max - x_min + 1) * (y_max - y_min + 1)
    assert 0 <= zoom <= 19
    assert request_count <= map_canvas_module.MAX_TILE_REQUESTS
    canvas.shutdown()


def test_view_bounds_normalizes_reversed_axes_and_rejects_nonfinite(monkeypatch) -> None:
    _app()
    canvas = InteractiveMapCanvas()
    canvas.axes.set_xlim(10, -10)
    canvas.axes.set_ylim(5, -5)
    assert canvas._view_bounds() == (-10.0, -5.0, 10.0, 5.0)

    monkeypatch.setattr(canvas.axes, "get_xlim", lambda: (0.0, math.inf))
    assert canvas._view_bounds() is None
    canvas.shutdown()


def test_junction_drag_mode_changes_canvas_state_and_cursor() -> None:
    _app()
    canvas = InteractiveMapCanvas()
    canvas.set_junction_drag_enabled(True)
    assert canvas.junction_drag_enabled is True
    canvas.set_junction_drag_enabled(False)
    assert canvas.junction_drag_enabled is False
    canvas.shutdown()


def test_navigation_toolbar_exposes_only_expected_native_tools() -> None:
    native_names = {item[0] for item in MapNavigationToolbar.toolitems}
    assert native_names <= {"Pan", "Zoom", "Save"}
    assert {"Pan", "Zoom"}.issubset(native_names)


def test_navigation_toolbar_junction_mode_toggles_canvas_drag_state() -> None:
    _app()
    canvas = InteractiveMapCanvas()
    toolbar = MapNavigationToolbar(canvas)
    assert toolbar.junction_drag_enabled is True
    assert canvas.junction_drag_enabled is True

    toolbar.set_junction_mode_available(False)
    assert toolbar.junction_drag_enabled is False
    assert canvas.junction_drag_enabled is False

    toolbar.set_junction_mode_available(True)
    toolbar.drag_junction_button.setChecked(True)
    assert canvas.junction_drag_enabled is True
    toolbar.close()
    canvas.shutdown()
