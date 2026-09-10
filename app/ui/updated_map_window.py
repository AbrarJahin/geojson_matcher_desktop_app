from __future__ import annotations

import html
import logging
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.pipeline import FinalizationResult, RoadMatchingPipeline
from app.ui.main_window import MainWindow as BaseMainWindow
from app.ui.map_canvas import (
    COUNTY_1_ACTIVE_COLOR,
    COUNTY_2_ACTIVE_COLOR,
    WEB_MERCATOR_CRS,
    InteractiveMapCanvas,
    MapNavigationToolbar,
)

LOGGER = logging.getLogger(__name__)

REVIEW_BUTTON_TEXT = "Open / Resume Manual Review"
UPDATED_MAP_BUTTON_TEXT = "Open Updated Map"


class UpdatedMapDialog(QDialog):
    """Map-only viewer for the two finalized GeoJSON outputs.

    The existing InteractiveMapCanvas and MapNavigationToolbar are reused so
    pan, zoom, save-image, OpenStreetMap tile loading, throttling, caching and
    request cancellation behave the same way as the manual-review map.
    """

    def __init__(
        self,
        pipeline: RoadMatchingPipeline,
        result: FinalizationResult,
        *,
        include_basemap: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.pipeline = pipeline
        self.result = result
        self._closed = False

        application = QApplication.instance()
        if application is not None and not application.windowIcon().isNull():
            self.setWindowIcon(application.windowIcon())
        self.setWindowTitle("Updated GeoJSON Map")
        self.resize(1200, 800)
        self.setSizeGripEnabled(True)

        self.canvas = InteractiveMapCanvas(self)
        self.toolbar = MapNavigationToolbar(self.canvas, self)
        self.toolbar.set_junction_mode_available(False)

        self.axes_button = QToolButton(self.toolbar)
        self.axes_button.setText("Axes")
        self.axes_button.setToolTip("Show or hide coordinate axes and tick marks.")
        self.axes_button.setCheckable(True)
        self.axes_button.setChecked(True)
        self.axes_button.toggled.connect(self._set_axes_visible)

        self.basemap_button = QToolButton(self.toolbar)
        self.basemap_button.setText("Street Basemap")
        self.basemap_button.setToolTip("Show or hide the online OpenStreetMap basemap.")
        self.basemap_button.setCheckable(True)
        self.basemap_button.setChecked(bool(include_basemap))
        self.basemap_button.toggled.connect(self._set_basemap_visible)

        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.axes_button)
        self.toolbar.addWidget(self.basemap_button)
        self.toolbar.addSeparator()

        county_1_name = html.escape(str(getattr(pipeline, "county_1_name", "GeoJSON 1")))
        county_2_name = html.escape(str(getattr(pipeline, "county_2_name", "GeoJSON 2")))
        legend = QLabel(
            f"<span style='color:{COUNTY_1_ACTIVE_COLOR}; font-size:18px'>●</span> "
            f"{county_1_name}&nbsp;&nbsp;&nbsp;"
            f"<span style='color:{COUNTY_2_ACTIVE_COLOR}; font-size:18px'>●</span> "
            f"{county_2_name}"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setToolTip(
            "The two finalized GeoJSON files are drawn in different colors so "
            "border connections are easy to inspect."
        )
        self.toolbar.addWidget(legend)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

        self._draw_updated_outputs()
        QTimer.singleShot(0, self.showMaximized)

    @staticmethod
    def _load_output_layer(path: Path) -> gpd.GeoDataFrame:
        """Read one finalized GeoJSON and keep only drawable road lines."""

        output_path = Path(path)
        if not output_path.is_file():
            raise FileNotFoundError(f"Updated GeoJSON file was not found: {output_path}")

        frame = gpd.read_file(output_path, engine="pyogrio")
        if frame.crs is None:
            # RFC 7946 GeoJSON coordinates are WGS84 longitude/latitude.
            frame = frame.set_crs("EPSG:4326")

        frame = frame.loc[
            frame.geometry.notna()
            & ~frame.geometry.is_empty
            & frame.geom_type.isin(["LineString", "MultiLineString"])
        ].copy()
        if frame.empty:
            raise ValueError(f"No drawable road geometry was found in {output_path.name}.")

        return frame.to_crs(WEB_MERCATOR_CRS)

    @staticmethod
    def _combined_bounds(
        first: gpd.GeoDataFrame,
        second: gpd.GeoDataFrame,
    ) -> tuple[float, float, float, float]:
        """Return padded Web-Mercator bounds covering both finalized layers."""

        def validated_bounds(
            frame: gpd.GeoDataFrame,
            layer_name: str,
        ) -> tuple[float, float, float, float]:
            try:
                values = tuple(float(value) for value in frame.total_bounds)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{layer_name} contains invalid map-bound coordinates."
                ) from exc

            if len(values) != 4:
                raise ValueError(
                    f"{layer_name} did not provide the expected four map bounds."
                )
            if not all(math.isfinite(value) for value in values):
                raise ValueError(
                    f"{layer_name} bounds contain non-finite coordinates."
                )

            west, south, east, north = values
            if west > east or south > north:
                raise ValueError(
                    f"{layer_name} bounds have an invalid extent ordering."
                )
            return west, south, east, north

        bounds_1 = validated_bounds(first, "Updated GeoJSON 1")
        bounds_2 = validated_bounds(second, "Updated GeoJSON 2")
        west = min(bounds_1[0], bounds_2[0])
        south = min(bounds_1[1], bounds_2[1])
        east = max(bounds_1[2], bounds_2[2])
        north = max(bounds_1[3], bounds_2[3])

        width = max(east - west, 1.0)
        height = max(north - south, 1.0)
        padding = max(max(width, height) * 0.03, 20.0)
        return (
            west - padding,
            south - padding,
            east + padding,
            north + padding,
        )

    def _draw_updated_outputs(self) -> None:
        """Draw the exact two files produced by finalization."""

        first = self._load_output_layer(self.result.county_1_output)
        second = self._load_output_layer(self.result.county_2_output)
        bounds = self._combined_bounds(first, second)

        canvas = self.canvas
        canvas._draw_token += 1
        token = canvas._draw_token
        canvas._basemap_refresh_timer.stop()
        canvas._pending_basemap_bounds = None
        canvas._current_pipeline = self.pipeline
        canvas._basemap_enabled = bool(self.basemap_button.isChecked())
        canvas._cancel_tile_requests()

        axes = canvas.axes
        canvas._suspend_view_refresh = True
        canvas._remove_basemap_artists()
        axes.clear()
        canvas._reset_road_hover()

        county_names = (
            str(getattr(self.pipeline, "county_1_name", "GeoJSON 1")),
            str(getattr(self.pipeline, "county_2_name", "GeoJSON 2")),
        )
        for frame, color, county_name in (
            (first, COUNTY_1_ACTIVE_COLOR, county_names[0]),
            (second, COUNTY_2_ACTIVE_COLOR, county_names[1]),
        ):
            for geometry in frame.geometry:
                artists = canvas._plot_geometry(
                    axes,
                    geometry,
                    color=color,
                    linewidth=2.2,
                    alpha=0.90,
                    zorder=5,
                )
                canvas._register_road_hover(artists, county_name)

        axes.set_xlim(bounds[0], bounds[2])
        axes.set_ylim(bounds[1], bounds[3])
        axes.set_aspect("equal")
        axes.set_xlabel("Web Mercator X coordinate (meters)")
        axes.set_ylabel("Web Mercator Y coordinate (meters)")
        axes.grid(True, alpha=0.35)

        canvas._suspend_view_refresh = False
        canvas._connect_view_limit_callbacks()
        self._set_axes_visible(self.axes_button.isChecked())
        canvas.draw_idle()

        if self.basemap_button.isChecked():
            canvas._schedule_basemap(token, bounds, self.pipeline)

    def _set_axes_visible(self, visible: bool) -> None:
        """Toggle only axis decorations; road and basemap artists stay intact."""

        if visible:
            self.canvas.axes.set_axis_on()
            self.canvas.figure.subplots_adjust(
                left=0.075,
                right=0.995,
                bottom=0.085,
                top=0.995,
            )
        else:
            self.canvas.axes.set_axis_off()
            self.canvas.figure.subplots_adjust(
                left=0.005,
                right=0.995,
                bottom=0.005,
                top=0.995,
            )
        self.canvas.draw_idle()

    def _set_basemap_visible(self, visible: bool) -> None:
        """Toggle the existing asynchronous OpenStreetMap basemap machinery."""

        canvas = self.canvas
        if canvas._shutting_down:
            return

        canvas._basemap_refresh_timer.stop()
        canvas._pending_basemap_bounds = None
        canvas._draw_token += 1
        token = canvas._draw_token
        canvas._cancel_tile_requests()
        canvas._basemap_enabled = bool(visible)
        canvas._current_pipeline = self.pipeline

        if not visible:
            canvas._remove_basemap_artists()
            canvas.draw_idle()
            return

        bounds = canvas._view_bounds()
        if bounds is not None:
            canvas._schedule_basemap(token, bounds, self.pipeline)

    def done(self, result: int) -> None:
        if not self._closed:
            self._closed = True
            self.canvas.shutdown()
        super().done(result)


class MainWindow(BaseMainWindow):
    """Existing main window plus a post-finalization updated-map viewer.

    Analysis, review, junction application and final output generation remain
    entirely in BaseMainWindow and the existing pipeline. Only the UI state
    after successful finalization is extended here.
    """

    def __init__(self) -> None:
        super().__init__()
        self._updated_map_result: FinalizationResult | None = None

    def _analysis_completed(self, pipeline: RoadMatchingPipeline) -> None:
        # A newly completed initial analysis is back in review mode.
        self._updated_map_result = None
        self.review_button.setText(REVIEW_BUTTON_TEXT)
        super()._analysis_completed(pipeline)

    def _open_review(self) -> None:
        # BaseMainWindow connected the button to self._open_review during its
        # constructor, so polymorphic dispatch reaches this method without
        # changing any existing signal wiring.
        if self._updated_map_result is not None:
            self._open_updated_map()
            return
        super()._open_review()

    def _finalization_completed(self, result: FinalizationResult) -> None:
        # Preserve the complete existing finalization-complete behavior first.
        # QMessageBox.information() is modal, so the lines below run only after
        # the user presses OK on "Road matching complete".
        super()._finalization_completed(result)

        self._updated_map_result = result
        self.review_button.setText(UPDATED_MAP_BUTTON_TEXT)
        self.review_button.setEnabled(True)

    def _open_updated_map(self) -> None:
        result = self._updated_map_result
        if result is None or self.pipeline is None:
            return

        try:
            dialog = UpdatedMapDialog(
                self.pipeline,
                result,
                include_basemap=self.basemap_checkbox.isChecked(),
                parent=self,
            )
        except Exception as exc:
            LOGGER.exception("Could not open the finalized GeoJSON map.")
            QMessageBox.critical(
                self,
                "Could not open updated map",
                f"The finalized GeoJSON map could not be opened.\n\n{exc}",
            )
            return

        dialog.exec()
