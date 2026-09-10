from __future__ import annotations

import math
import time
from collections import OrderedDict
from typing import Any

import geopandas as gpd
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import ScalarFormatter
from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QToolButton, QWidget
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.ops import transform as shapely_transform

from app import __version__
from app.core.junctions import update_member_geometry

WEB_MERCATOR_CRS = "EPSG:3857"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
WEB_MERCATOR_WORLD = WEB_MERCATOR_HALF_WORLD * 2.0
TILE_SIZE = 256
MAX_TILE_REQUESTS = 16
BASEMAP_VIEW_REFRESH_INTERVAL_MS = 5_000

# County colors use related hue families so county identity remains obvious,
# while active/current-junction roads remain visually stronger than context.
COUNTY_1_ACTIVE_COLOR = "#0057B8"
COUNTY_1_CONTEXT_COLOR = "#324770"
COUNTY_2_ACTIVE_COLOR = "#D95F02"
COUNTY_2_CONTEXT_COLOR = "#D9B28D"
ACTIVE_ROAD_ALPHA = 0.40
CONTEXT_ROAD_ALPHA = 0.90


class InteractiveMapCanvas(FigureCanvasQTAgg):
    junction_point_changed = Signal(float, float)
    """Matplotlib road review map with non-blocking Qt-native tile requests.

    No QRunnable, Python worker thread, contextily, rasterio, or GDAL operation
    is used by the review map. Roads are transformed to Web Mercator for the
    display only; the analytical pipeline remains in its configured CRS.
    """

    _BASEMAP_CACHE_LIMIT = 8

    def __init__(self, parent: QWidget | None = None):
        self.figure = Figure(figsize=(9, 7), tight_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self.setParent(parent)

        self._drag_state: (
            tuple[float, float, tuple[float, float], tuple[float, float]] | None
        ) = None
        self._junction_dragging = False
        self._junction_marker_artist: Any | None = None
        self._junction_point_web: Point | None = None
        self._junction_source_crs: str | None = None
        self._junction_drag_enabled = False
        self._junction_preview_sources: dict[str, dict[str, Any]] = {}
        self._junction_connector_artists: dict[str, Any] = {}
        self._hover_roads: list[tuple[Any, str]] = []
        self._hover_annotation: Any | None = None
        self._draw_token = 0
        self._shutting_down = False
        self._transformers: dict[str, Transformer] = {}

        # Vector roads respond immediately. Online basemap refreshes are
        # throttled independently and use the newest visible viewport.
        self._current_pipeline: Any | None = None
        self._basemap_enabled = False
        self._pending_basemap_bounds: (
            tuple[float, float, float, float] | None
        ) = None
        self._last_basemap_refresh_at = 0.0
        self._suspend_view_refresh = False

        # Keep references to the currently displayed basemap artists so that
        # a refreshed basemap replaces the previous one instead of stacking.
        self._basemap_image_artist: Any | None = None
        self._basemap_attribution_artist: Any | None = None

        self._basemap_refresh_timer = QTimer(self)
        self._basemap_refresh_timer.setSingleShot(True)
        self._basemap_refresh_timer.timeout.connect(
            self._run_pending_basemap_refresh
        )

        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._network_reply_finished)
        self._reply_context: dict[QNetworkReply, dict[str, Any]] = {}
        self._tile_batches: dict[int, dict[str, Any]] = {}
        self._basemap_cache: OrderedDict[
            tuple[int, int, int, int, int], tuple[np.ndarray, tuple[float, ...]]
        ] = OrderedDict()

        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("button_release_event", self._on_release)

    def _toolbar_is_active(self) -> bool:
        toolbar = getattr(self, "toolbar", None)
        return bool(toolbar and getattr(toolbar, "mode", ""))

    def set_junction_drag_enabled(self, enabled: bool) -> None:
        """Enable or disable the dedicated shared-junction drag interaction."""

        self._junction_drag_enabled = bool(enabled)
        self._drag_state = None
        self._junction_dragging = False
        if self._junction_drag_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    @property
    def junction_drag_enabled(self) -> bool:
        return bool(self._junction_drag_enabled)

    def _connect_view_limit_callbacks(self) -> None:
        """Observe zoom, pan, Home, Back, and Forward viewport changes.

        Axes.clear() removes these Matplotlib callback registrations,
        so this method is called after every complete pair redraw.
        """
        self.axes.callbacks.connect(
            "xlim_changed",
            self._view_limits_changed,
        )
        self.axes.callbacks.connect(
            "ylim_changed",
            self._view_limits_changed,
        )

    def _view_bounds(
        self,
    ) -> tuple[float, float, float, float] | None:
        """Return normalized current map bounds."""

        x_1, x_2 = self.axes.get_xlim()
        y_1, y_2 = self.axes.get_ylim()

        bounds = (
            min(float(x_1), float(x_2)),
            min(float(y_1), float(y_2)),
            max(float(x_1), float(x_2)),
            max(float(y_1), float(y_2)),
        )

        if not all(math.isfinite(value) for value in bounds):
            return None

        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return None

        return bounds

    def _view_limits_changed(self, _: Any) -> None:
        """Handle Matplotlib viewport changes."""

        if self._suspend_view_refresh or self._shutting_down:
            return

        self.request_basemap_refresh()

    def request_basemap_refresh(self) -> None:
        """Queue a basemap refresh for the newest viewport.

        Repeated zoom or pan events update the pending bounds without
        restarting the timer. This ensures no more than one online
        basemap refresh in each five-second interval.
        """

        if (
            self._shutting_down
            or not self._basemap_enabled
            or self._current_pipeline is None
        ):
            return

        bounds = self._view_bounds()
        if bounds is None:
            return

        # Always retain the newest visible viewport.
        self._pending_basemap_bounds = bounds

        elapsed_ms = int(
            max(
                0.0,
                time.monotonic() - self._last_basemap_refresh_at,
            )
            * 1000
        )

        delay_ms = max(
            0,
            BASEMAP_VIEW_REFRESH_INTERVAL_MS - elapsed_ms,
        )

        # Do not restart an existing timer. Zoom and pan events only
        # replace the pending bounds.
        if not self._basemap_refresh_timer.isActive():
            self._basemap_refresh_timer.start(delay_ms)

    def _run_pending_basemap_refresh(self) -> None:
        """Download tiles for the most recent queued viewport."""

        if (
            self._shutting_down
            or not self._basemap_enabled
            or self._current_pipeline is None
            or self._pending_basemap_bounds is None
        ):
            self._pending_basemap_bounds = None
            return

        bounds = self._pending_basemap_bounds
        self._pending_basemap_bounds = None

        # Invalidate and cancel any older tile request.
        self._draw_token += 1
        token = self._draw_token
        self._cancel_tile_requests()

        self._schedule_basemap(
            token,
            bounds,
            self._current_pipeline,
        )

    def _remove_basemap_artists(self) -> None:
        """Remove the existing basemap and attribution artists."""

        for attribute in (
            "_basemap_image_artist",
            "_basemap_attribution_artist",
        ):
            artist = getattr(self, attribute, None)
            if artist is None:
                continue

            try:
                artist.remove()
            except (AttributeError, RuntimeError, ValueError):
                pass

            setattr(self, attribute, None)

    def _on_scroll(self, event: Any) -> None:
        if event.xdata is None or event.ydata is None:
            return
        ax = self.axes
        x_min, x_max = ax.get_xlim()
        y_min, y_max = ax.get_ylim()
        scale = 0.80 if event.button == "up" else 1.25
        new_width = (x_max - x_min) * scale
        new_height = (y_max - y_min) * scale
        rel_x = (event.xdata - x_min) / max(x_max - x_min, 1e-12)
        rel_y = (event.ydata - y_min) / max(y_max - y_min, 1e-12)
        ax.set_xlim(
            event.xdata - new_width * rel_x,
            event.xdata + new_width * (1 - rel_x),
        )
        ax.set_ylim(
            event.ydata - new_height * rel_y,
            event.ydata + new_height * (1 - rel_y),
        )
        self.draw_idle()

    def _on_press(self, event: Any) -> None:
        if event.button != 1 or event.xdata is None or event.ydata is None:
            return

        # The dedicated junction tool owns left-drag while it is active.  This
        # makes the interaction deterministic even after checkboxes/buttons are
        # used, and gives Pan/Zoom their own explicit toolbar modes.
        if self._junction_drag_enabled:
            if (
                self._junction_marker_artist is not None
                and self._junction_point_web is not None
                and event.x is not None
                and event.y is not None
            ):
                marker_x, marker_y = self.axes.transData.transform(
                    (self._junction_point_web.x, self._junction_point_web.y)
                )
                if (
                    math.hypot(float(event.x) - marker_x, float(event.y) - marker_y)
                    <= 18.0
                ):
                    self._junction_dragging = True
                    self._drag_state = None
            return

        if self._toolbar_is_active():
            return

        self._drag_state = (
            event.xdata,
            event.ydata,
            self.axes.get_xlim(),
            self.axes.get_ylim(),
        )

    def _on_motion(self, event: Any) -> None:
        if event.xdata is None or event.ydata is None:
            if not self._junction_dragging and self._drag_state is None:
                self._update_road_hover(event)
            return

        if self._junction_dragging and self._junction_marker_artist is not None:
            self._junction_point_web = Point(float(event.xdata), float(event.ydata))
            self._junction_marker_artist.set_offsets(
                np.array([[float(event.xdata), float(event.ydata)]])
            )
            if self._junction_source_crs:
                projected = self._from_web_mercator(
                    self._junction_point_web, self._junction_source_crs
                )
                self._update_junction_preview(projected)
                self.junction_point_changed.emit(float(projected.x), float(projected.y))
            self.draw_idle()
            return

        if self._drag_state is None:
            self._update_road_hover(event)
            return
        start_x, start_y, x_limits, y_limits = self._drag_state
        dx = event.xdata - start_x
        dy = event.ydata - start_y
        self.axes.set_xlim(x_limits[0] - dx, x_limits[1] - dx)
        self.axes.set_ylim(y_limits[0] - dy, y_limits[1] - dy)
        self.draw_idle()

    def _reset_road_hover(self) -> None:
        """Clear road-hover artists before a complete map redraw."""

        self._hover_roads = []
        self._hover_annotation = None

    def _register_road_hover(self, artists: list[Any], county_name: str) -> None:
        """Register plotted road lines for a lightweight county-name tooltip."""

        label = str(county_name).strip() or "County"
        for artist in artists:
            # A slightly generous pick radius makes thin context roads practical
            # to hover without changing their visible line width.
            artist.set_pickradius(6.0)
            self._hover_roads.append((artist, label))

    def _ensure_hover_annotation(self) -> Any:
        if self._hover_annotation is None:
            self._hover_annotation = self.axes.annotate(
                "",
                xy=(0.0, 0.0),
                xytext=(10, 10),
                textcoords="offset points",
                bbox={
                    "boxstyle": "round,pad=0.3",
                    "fc": "#202020",
                    "ec": "#f0f0f0",
                    "alpha": 0.92,
                },
                color="white",
                fontsize=9,
                zorder=30,
            )
            self._hover_annotation.set_visible(False)
        return self._hover_annotation

    def _update_road_hover(self, event: Any) -> None:
        """Show the county name when the pointer is over any displayed road."""

        annotation = self._ensure_hover_annotation()
        if event.inaxes is not self.axes or event.xdata is None or event.ydata is None:
            if annotation.get_visible():
                annotation.set_visible(False)
                self.draw_idle()
            return

        # Reverse plot order so thick/current-junction roads win when an active
        # road is drawn over the same context geometry.
        for artist, county_name in reversed(self._hover_roads):
            try:
                contains, _ = artist.contains(event)
            except (AttributeError, RuntimeError, ValueError):
                continue
            if not contains:
                continue
            annotation.xy = (float(event.xdata), float(event.ydata))
            annotation.set_text(county_name)
            annotation.set_visible(True)
            self.draw_idle()
            return

        if annotation.get_visible():
            annotation.set_visible(False)
            self.draw_idle()

    def _on_release(self, _: Any) -> None:
        self._drag_state = None
        self._junction_dragging = False

    @staticmethod
    def _plot_geometry(ax: Any, geometry: Any, **kwargs: Any) -> list[Any]:
        if geometry is None or geometry.is_empty:
            return []
        if geometry.geom_type == "LineString":
            parts = [geometry]
        elif geometry.geom_type == "MultiLineString":
            parts = list(geometry.geoms)
        else:
            return []
        label = kwargs.pop("label", None)
        artists: list[Any] = []
        for index, part in enumerate(parts):
            x_values, y_values = part.xy
            line = ax.plot(
                x_values,
                y_values,
                label=label if index == 0 else None,
                **kwargs,
            )[0]
            artists.append(line)
        return artists

    @staticmethod
    def _geometry_parts(geometry: Any) -> list[Any]:
        if geometry is None or geometry.is_empty:
            return []
        if geometry.geom_type == "LineString":
            return [geometry]
        if geometry.geom_type == "MultiLineString":
            return list(geometry.geoms)
        return []

    def _update_junction_preview(self, junction_projected: Point) -> None:
        """Move all selected-road preview geometries to the dragged junction."""

        if not self._junction_source_crs or self._junction_point_web is None:
            return

        for member_key, source in self._junction_preview_sources.items():
            member = source["member"]
            original_geometry = source["geometry"]
            artists = source["artists"]
            try:
                preview_geometry, _ = update_member_geometry(
                    original_geometry, member, junction_projected
                )
                preview_web = self._to_web_mercator(
                    preview_geometry, self._junction_source_crs
                )
            except Exception:
                continue

            parts = self._geometry_parts(preview_web)
            if len(parts) == len(artists):
                for artist, part in zip(artists, parts):
                    x_values, y_values = part.xy
                    artist.set_data(x_values, y_values)

            connector = self._junction_connector_artists.get(member_key)
            contact_web = source.get("contact_web")
            if connector is not None and contact_web is not None:
                connector.set_data(
                    [contact_web.x, self._junction_point_web.x],
                    [contact_web.y, self._junction_point_web.y],
                )

    @staticmethod
    def _visible_roads(
        layer: gpd.GeoDataFrame, bounds: tuple[float, float, float, float]
    ) -> gpd.GeoDataFrame:
        viewport = box(*bounds)
        try:
            positions = list(layer.sindex.intersection(viewport.bounds))
            subset = layer.iloc[positions].copy()
        except Exception:
            subset = layer.copy()
        if subset.empty:
            return subset
        subset = subset.loc[
            subset.geometry.notna() & ~subset.geometry.is_empty
        ].copy()
        if subset.empty:
            return subset
        return subset.loc[subset.geometry.intersects(viewport)].copy()

    @staticmethod
    def _square_bounds(
        geometries: list[Any], minimum_padding: float = 20.0
    ) -> tuple[float, float, float, float]:
        valid = [
            geometry
            for geometry in geometries
            if geometry is not None and not geometry.is_empty
        ]
        if not valid:
            raise ValueError(
                "No drawable geometry was available for the selected road pair."
            )
        min_x = min(float(geometry.bounds[0]) for geometry in valid)
        min_y = min(float(geometry.bounds[1]) for geometry in valid)
        max_x = max(float(geometry.bounds[2]) for geometry in valid)
        max_y = max(float(geometry.bounds[3]) for geometry in valid)
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        padding_x = max(width * 0.20, minimum_padding)
        padding_y = max(height * 0.20, minimum_padding)
        side = max(width + 2 * padding_x, height + 2 * padding_y)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        half = side / 2.0
        return (
            center_x - half,
            center_y - half,
            center_x + half,
            center_y + half,
        )

    def _transformer(self, source_crs: str) -> Transformer:
        key = str(source_crs)
        transformer = self._transformers.get(key)
        if transformer is None:
            transformer = Transformer.from_crs(
                source_crs, WEB_MERCATOR_CRS, always_xy=True
            )
            self._transformers[key] = transformer
        return transformer

    def _to_web_mercator(self, geometry: Any, source_crs: str) -> Any:
        if geometry is None or geometry.is_empty:
            return geometry
        return shapely_transform(self._transformer(source_crs).transform, geometry)

    def _from_web_mercator(self, geometry: Any, target_crs: str) -> Any:
        if geometry is None or geometry.is_empty:
            return geometry
        transformer = Transformer.from_crs(
            WEB_MERCATOR_CRS, target_crs, always_xy=True
        )
        return shapely_transform(transformer.transform, geometry)

    @staticmethod
    def _tile_x(meters_x: float, zoom: int) -> float:
        return (
            (meters_x + WEB_MERCATOR_HALF_WORLD)
            / WEB_MERCATOR_WORLD
            * (2**zoom)
        )

    @staticmethod
    def _tile_y(meters_y: float, zoom: int) -> float:
        return (
            (WEB_MERCATOR_HALF_WORLD - meters_y)
            / WEB_MERCATOR_WORLD
            * (2**zoom)
        )

    @staticmethod
    def _meters_x(tile_x: float, zoom: int) -> float:
        return (
            tile_x / (2**zoom) * WEB_MERCATOR_WORLD
            - WEB_MERCATOR_HALF_WORLD
        )

    @staticmethod
    def _meters_y(tile_y: float, zoom: int) -> float:
        return (
            WEB_MERCATOR_HALF_WORLD
            - tile_y / (2**zoom) * WEB_MERCATOR_WORLD
        )

    def _tile_grid(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int, int]:
        west, south, east, north = bounds
        pixel_width = max(int(self.width()), 800)
        desired_mpp = max((east - west) / pixel_width, 0.01)
        initial_zoom = int(
            math.floor(
                math.log2(
                    WEB_MERCATOR_WORLD
                    / (TILE_SIZE * desired_mpp)
                )
            )
        )
        zoom = max(0, min(initial_zoom, 19))

        while True:
            tile_count = 2**zoom
            x_min = max(
                0,
                min(
                    tile_count - 1,
                    int(math.floor(self._tile_x(west, zoom))),
                ),
            )
            x_max = max(
                0,
                min(
                    tile_count - 1,
                    int(math.floor(self._tile_x(east, zoom))),
                ),
            )
            y_min = max(
                0,
                min(
                    tile_count - 1,
                    int(math.floor(self._tile_y(north, zoom))),
                ),
            )
            y_max = max(
                0,
                min(
                    tile_count - 1,
                    int(math.floor(self._tile_y(south, zoom))),
                ),
            )
            requests = (x_max - x_min + 1) * (y_max - y_min + 1)
            if requests <= MAX_TILE_REQUESTS or zoom == 0:
                return zoom, x_min, y_min, x_max, y_max
            zoom -= 1

    def _cancel_tile_requests(self) -> None:
        replies = list(self._reply_context)
        self._reply_context.clear()
        self._tile_batches.clear()
        for reply in replies:
            try:
                reply.abort()
            except RuntimeError:
                pass
            reply.deleteLater()

    def _schedule_basemap(
        self,
        token: int,
        bounds: tuple[float, float, float, float],
        pipeline: Any,
    ) -> None:
        if self._shutting_down or token != self._draw_token:
            return

        self._last_basemap_refresh_at = time.monotonic()

        zoom, x_min, y_min, x_max, y_max = self._tile_grid(
            bounds
        )
        cache_key = (zoom, x_min, y_min, x_max, y_max)
        cached = self._basemap_cache.get(cache_key)
        if cached is not None:
            image, extent = cached
            self._apply_basemap(token, image, extent)
            return

        total = (x_max - x_min + 1) * (y_max - y_min + 1)
        batch = {
            "remaining": total,
            "images": {},
            "errors": [],
            "zoom": zoom,
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "pipeline": pipeline,
            "cache_key": cache_key,
        }
        self._tile_batches[token] = batch

        user_agent = (
            f"RoadMatcherDesktop/{__version__} "
            "(manual road-pair research review)"
        ).encode("ascii")
        for tile_y in range(y_min, y_max + 1):
            for tile_x in range(x_min, x_max + 1):
                url = QUrl(
                    f"https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png"
                )
                request = QNetworkRequest(url)
                request.setRawHeader(b"User-Agent", user_agent)
                request.setRawHeader(b"Accept", b"image/png,image/*;q=0.8")
                reply = self._network.get(request)
                timeout = QTimer(reply)
                timeout.setSingleShot(True)
                timeout.setInterval(7000)
                timeout.timeout.connect(reply.abort)
                timeout.start()
                self._reply_context[reply] = {
                    "token": token,
                    "tile_x": tile_x,
                    "tile_y": tile_y,
                    "timer": timeout,
                }

    def _network_reply_finished(self, reply: QNetworkReply) -> None:
        context = self._reply_context.pop(reply, None)
        if context is None:
            reply.deleteLater()
            return

        timer: QTimer = context["timer"]
        timer.stop()
        token = int(context["token"])
        batch = self._tile_batches.get(token)
        if batch is None or token != self._draw_token or self._shutting_down:
            reply.deleteLater()
            return

        if reply.error() == QNetworkReply.NetworkError.NoError:
            image = QImage.fromData(bytes(reply.readAll()))
            if image.isNull():
                batch["errors"].append(
                    "A downloaded basemap tile was not a valid image."
                )
            else:
                batch["images"][
                    (context["tile_x"], context["tile_y"])
                ] = image
        else:
            batch["errors"].append(reply.errorString())

        batch["remaining"] -= 1
        reply.deleteLater()
        if batch["remaining"] <= 0:
            self._finish_tile_batch(token)

    def _finish_tile_batch(self, token: int) -> None:
        batch = self._tile_batches.pop(token, None)
        if batch is None or token != self._draw_token or self._shutting_down:
            return

        images: dict[tuple[int, int], QImage] = batch["images"]
        pipeline = batch["pipeline"]
        if not images:
            errors = sorted(
                set(str(value) for value in batch["errors"] if value)
            )
            detail = (
                f" Details: {' | '.join(errors[:3])}"
                if errors
                else ""
            )
            pipeline.log(
                "Online basemap unavailable; vector roads remain visible."
                + detail
            )
            return

        x_min = int(batch["x_min"])
        x_max = int(batch["x_max"])
        y_min = int(batch["y_min"])
        y_max = int(batch["y_max"])
        zoom = int(batch["zoom"])
        width = (x_max - x_min + 1) * TILE_SIZE
        height = (y_max - y_min + 1) * TILE_SIZE
        mosaic = QImage(
            width,
            height,
            QImage.Format.Format_RGBA8888,
        )
        mosaic.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mosaic)
        try:
            for (tile_x, tile_y), image in images.items():
                painter.drawImage(
                    (tile_x - x_min) * TILE_SIZE,
                    (tile_y - y_min) * TILE_SIZE,
                    image,
                )
        finally:
            painter.end()

        rgba = mosaic.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        bytes_per_line = rgba.bytesPerLine()
        raw = np.frombuffer(
            rgba.constBits(),
            dtype=np.uint8,
            count=rgba.sizeInBytes(),
        ).reshape((rgba.height(), bytes_per_line))
        array = raw[
            :, : rgba.width() * 4
        ].reshape(
            (rgba.height(), rgba.width(), 4)
        ).copy()

        left = self._meters_x(x_min, zoom)
        right = self._meters_x(x_max + 1, zoom)
        top = self._meters_y(y_min, zoom)
        bottom = self._meters_y(y_max + 1, zoom)
        extent = (left, right, bottom, top)
        cache_key = batch["cache_key"]
        self._basemap_cache[cache_key] = (array, extent)
        self._basemap_cache.move_to_end(cache_key)
        while len(self._basemap_cache) > self._BASEMAP_CACHE_LIMIT:
            self._basemap_cache.popitem(last=False)

        self._apply_basemap(token, array, extent)

    def _apply_basemap(
        self,
        token: int,
        image: np.ndarray,
        extent: tuple[float, ...],
    ) -> None:
        if token != self._draw_token or self._shutting_down:
            return

        current_xlim = self.axes.get_xlim()
        current_ylim = self.axes.get_ylim()

        # Adding the image and restoring the existing limits generates
        # Matplotlib limit-change events. Suppress refresh scheduling
        # during this internal operation.
        self._suspend_view_refresh = True

        try:
            self._remove_basemap_artists()

            self._basemap_image_artist = self.axes.imshow(
                image,
                extent=extent,
                interpolation="bilinear",
                origin="upper",
                zorder=0,
            )

            self._basemap_attribution_artist = self.axes.text(
                0.005,
                0.005,
                "© OpenStreetMap contributors",
                transform=self.axes.transAxes,
                fontsize=7,
                alpha=0.75,
                zorder=20,
            )

            # Keep the exact viewport selected by the user.
            self.axes.set_xlim(*current_xlim)
            self.axes.set_ylim(*current_ylim)

        finally:
            self._suspend_view_refresh = False

        self.draw_idle()

    def shutdown(self) -> None:
        """Abort network and scheduled map-refresh activity."""

        if self._shutting_down:
            return

        self._shutting_down = True
        self._draw_token += 1

        self._basemap_refresh_timer.stop()
        self._pending_basemap_bounds = None
        self._current_pipeline = None
        self._basemap_enabled = False
        self._junction_marker_artist = None
        self._junction_point_web = None
        self._junction_source_crs = None
        self._junction_dragging = False
        self._junction_preview_sources = {}
        self._junction_connector_artists = {}
        self._reset_road_hover()

        self._cancel_tile_requests()
        self._remove_basemap_artists()
        self._basemap_cache.clear()

    def draw_junction(
        self,
        pipeline: Any,
        proposal: Any,
        include_basemap: bool = True,
    ) -> None:
        """Draw one multi-road junction and expose its shared point for dragging."""
        if self._shutting_down:
            return
        self._draw_token += 1
        token = self._draw_token
        self._basemap_refresh_timer.stop()
        self._pending_basemap_bounds = None
        self._current_pipeline = pipeline
        self._basemap_enabled = bool(include_basemap)
        self._cancel_tile_requests()

        source_crs = pipeline.config.target_crs
        junction = Point(
            float(proposal.junction_x),
            float(proposal.junction_y),
        )
        members = list(proposal.members)
        member_geometries = [
            pipeline.junction_member_geometry(member)
            for member in members
        ]
        contacts = [member.contact_point for member in members]
        target_view = self._square_bounds(
            member_geometries + contacts + [junction]
        )

        web_geometries = [
            self._to_web_mercator(geometry, source_crs)
            for geometry in member_geometries
        ]
        web_contacts = [
            self._to_web_mercator(contact, source_crs)
            for contact in contacts
        ]
        junction_web = self._to_web_mercator(
            junction,
            source_crs,
        )
        web_view = self._square_bounds(
            web_geometries + web_contacts + [junction_web],
            minimum_padding=20.0,
        )

        ax = self.axes
        self._suspend_view_refresh = True
        self._remove_basemap_artists()
        ax.clear()
        self._reset_road_hover()
        self._junction_marker_artist = None
        self._junction_point_web = junction_web
        self._junction_source_crs = source_crs
        self._junction_preview_sources = {}
        self._junction_connector_artists = {}
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        county_context_styles = (
            (
                pipeline.county_1_name,
                COUNTY_1_CONTEXT_COLOR,
            ),
            (
                pipeline.county_2_name,
                COUNTY_2_CONTEXT_COLOR,
            ),
        )
        for layer, (
            county_name,
            context_color,
        ) in zip(
            pipeline.context_layers(),
            county_context_styles,
        ):
            visible = self._visible_roads(
                layer,
                target_view,
            )
            for road_geometry in visible.geometry:
                web_geometry = self._to_web_mercator(
                    road_geometry,
                    source_crs,
                )
                artists = self._plot_geometry(
                    ax,
                    web_geometry,
                    color=context_color,
                    linewidth=1.15,
                    alpha=CONTEXT_ROAD_ALPHA,
                    zorder=2,
                )
                self._register_road_hover(
                    artists,
                    county_name,
                )

        for member, geometry, contact_web in zip(
            members,
            member_geometries,
            web_contacts,
        ):
            selected = bool(member.selected)
            if not selected:
                color = "#8a8a8a"
                display_geometry = geometry
            else:
                color = (
                    COUNTY_1_ACTIVE_COLOR
                    if int(member.county_index) == 1
                    else COUNTY_2_ACTIVE_COLOR
                )
                try:
                    display_geometry, _ = update_member_geometry(
                        geometry,
                        member,
                        junction,
                    )
                except Exception:
                    display_geometry = geometry

            geometry_web = self._to_web_mercator(
                display_geometry,
                source_crs,
            )
            artists = self._plot_geometry(
                ax,
                geometry_web,
                color=color,
                linewidth=4.0 if selected else 2.2,
                alpha=(
                    ACTIVE_ROAD_ALPHA
                    if selected
                    else 0.45
                ),
                zorder=5 if selected else 4,
            )
            county_name = (
                pipeline.county_1_name
                if int(member.county_index) == 1
                else pipeline.county_2_name
            )
            self._register_road_hover(
                artists,
                county_name,
            )
            ax.scatter(
                contact_web.x,
                contact_web.y,
                s=55 if selected else 35,
                color=(
                    (
                        "#e74c3c"
                        if int(member.county_index) == 1
                        else "#39a852"
                    )
                    if selected
                    else "#8a8a8a"
                ),
                edgecolor="black",
                linewidth=0.7,
                alpha=0.70,
                zorder=7,
            )
            if selected:
                connector = ax.plot(
                    [
                        contact_web.x,
                        junction_web.x,
                    ],
                    [
                        contact_web.y,
                        junction_web.y,
                    ],
                    color="black",
                    linewidth=1.0,
                    linestyle="--",
                    alpha=0.40,
                    zorder=6,
                )[0]
                self._junction_preview_sources[
                    member.key
                ] = {
                    "member": member,
                    "geometry": geometry,
                    "artists": artists,
                    "contact_web": contact_web,
                }
                self._junction_connector_artists[
                    member.key
                ] = connector

        self._junction_marker_artist = ax.scatter(
            junction_web.x,
            junction_web.y,
            s=180,
            marker="X",
            color="limegreen",
            edgecolor="black",
            linewidth=1.1,
            zorder=9,
        )

        ax.set_title(
            f"{proposal.junction_id} — drag the green X to set the shared junction"
        )
        ax.grid(True, alpha=0.50)
        ax.set_xlabel(
            "Web Mercator X coordinate (meters)"
        )
        ax.set_ylabel(
            "Web Mercator Y coordinate (meters)"
        )
        x_formatter = ScalarFormatter(useOffset=False)
        y_formatter = ScalarFormatter(useOffset=False)
        x_formatter.set_scientific(False)
        y_formatter.set_scientific(False)
        ax.xaxis.set_major_formatter(x_formatter)
        ax.yaxis.set_major_formatter(y_formatter)
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        self._suspend_view_refresh = False
        self._connect_view_limit_callbacks()
        self.draw_idle()
        if include_basemap:
            self._schedule_basemap(
                token,
                web_view,
                pipeline,
            )

    def draw_pair(
        self,
        pipeline: Any,
        plan_row: Any,
        include_basemap: bool = True,
    ) -> tuple[str, str]:
        if self._shutting_down:
            return "", ""
        self._draw_token += 1
        token = self._draw_token

        self._basemap_refresh_timer.stop()
        self._pending_basemap_bounds = None
        self._current_pipeline = pipeline
        self._basemap_enabled = bool(include_basemap)

        self._cancel_tile_requests()
        self._junction_marker_artist = None
        self._junction_point_web = None
        self._junction_source_crs = None
        self._junction_dragging = False
        self._junction_preview_sources = {}
        self._junction_connector_artists = {}

        pair = pipeline.pair_features(
            plan_row["county_1_id"],
            plan_row["county_2_id"],
        )
        geometry_1 = pair["geometry_county1"]
        geometry_2 = pair["geometry_county2"]
        contact_1 = pair["county_1_contact_point"]
        contact_2 = pair["county_2_contact_point"]
        midpoint = Point(
            (contact_1.x + contact_2.x) / 2.0,
            (contact_1.y + contact_2.y) / 2.0,
        )

        target_view = self._square_bounds(
            [
                geometry_1,
                geometry_2,
                contact_1,
                contact_2,
            ]
        )
        source_crs = pipeline.config.target_crs
        geometry_1_web = self._to_web_mercator(
            geometry_1,
            source_crs,
        )
        geometry_2_web = self._to_web_mercator(
            geometry_2,
            source_crs,
        )
        contact_1_web = self._to_web_mercator(
            contact_1,
            source_crs,
        )
        contact_2_web = self._to_web_mercator(
            contact_2,
            source_crs,
        )
        midpoint_web = self._to_web_mercator(
            midpoint,
            source_crs,
        )
        web_view = self._square_bounds(
            [
                geometry_1_web,
                geometry_2_web,
                contact_1_web,
                contact_2_web,
            ],
            minimum_padding=20.0,
        )

        ax = self.axes

        # Suppress callbacks while constructing a complete new pair map.
        self._suspend_view_refresh = True
        self._remove_basemap_artists()
        ax.clear()
        self._reset_road_hover()
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        county_context_styles = (
            (
                pipeline.county_1_name,
                COUNTY_1_CONTEXT_COLOR,
            ),
            (
                pipeline.county_2_name,
                COUNTY_2_CONTEXT_COLOR,
            ),
        )
        for layer, (
            county_name,
            context_color,
        ) in zip(
            pipeline.context_layers(),
            county_context_styles,
        ):
            visible = self._visible_roads(
                layer,
                target_view,
            )
            for road_geometry in visible.geometry:
                web_geometry = self._to_web_mercator(
                    road_geometry,
                    source_crs,
                )
                artists = self._plot_geometry(
                    ax,
                    web_geometry,
                    color=context_color,
                    linewidth=1.25,
                    alpha=CONTEXT_ROAD_ALPHA,
                    zorder=2,
                )
                self._register_road_hover(
                    artists,
                    county_name,
                )

        name_1 = str(
            pair.get("full_road_label_county1")
            or pair.get("road_name_county1")
            or plan_row["county_1_id"]
        )
        name_2 = str(
            pair.get("full_road_label_county2")
            or pair.get("road_name_county2")
            or plan_row["county_2_id"]
        )
        county_1_artists = self._plot_geometry(
            ax,
            geometry_1_web,
            color=COUNTY_1_ACTIVE_COLOR,
            linewidth=4.0,
            alpha=ACTIVE_ROAD_ALPHA,
            zorder=5,
        )
        self._register_road_hover(
            county_1_artists,
            pipeline.county_1_name,
        )
        county_2_artists = self._plot_geometry(
            ax,
            geometry_2_web,
            color=COUNTY_2_ACTIVE_COLOR,
            linewidth=4.0,
            alpha=ACTIVE_ROAD_ALPHA,
            zorder=5,
        )
        self._register_road_hover(
            county_2_artists,
            pipeline.county_2_name,
        )
        ax.plot(
            [
                contact_1_web.x,
                contact_2_web.x,
            ],
            [
                contact_1_web.y,
                contact_2_web.y,
            ],
            color="black",
            linewidth=1.7,
            linestyle="--",
            alpha=0.90,
            zorder=6,
        )
        ax.scatter(
            contact_1_web.x,
            contact_1_web.y,
            s=65,
            color="red",
            alpha=0.50,
            edgecolor="black",
            linewidth=0.8,
            zorder=7,
        )
        ax.scatter(
            contact_2_web.x,
            contact_2_web.y,
            s=65,
            color="green",
            alpha=0.50,
            edgecolor="black",
            linewidth=0.8,
            zorder=7,
        )
        ax.scatter(
            midpoint_web.x,
            midpoint_web.y,
            s=150,
            marker="X",
            color="limegreen",
            edgecolor="black",
            linewidth=1.0,
            zorder=8,
        )

        ax.set_title("Possible connected roads")
        ax.grid(True, alpha=0.50)
        ax.set_xlabel(
            "Web Mercator X coordinate (meters)"
        )
        ax.set_ylabel(
            "Web Mercator Y coordinate (meters)"
        )
        x_formatter = ScalarFormatter(useOffset=False)
        y_formatter = ScalarFormatter(useOffset=False)
        x_formatter.set_scientific(False)
        y_formatter.set_scientific(False)
        ax.xaxis.set_major_formatter(x_formatter)
        ax.yaxis.set_major_formatter(y_formatter)
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        self._suspend_view_refresh = False
        self._connect_view_limit_callbacks()

        self.draw_idle()
        if include_basemap:
            self._schedule_basemap(
                token,
                web_view,
                pipeline,
            )
        return name_1, name_2


class MapNavigationToolbar(NavigationToolbar2QT):
    # Home/Back/Forward are intentionally omitted.  Junction review has a
    # dedicated editing mode, while Pan/Zoom/Save remain available explicitly.
    toolitems = tuple(
        item
        for item in NavigationToolbar2QT.toolitems
        if item[0] in {"Pan", "Zoom", "Save"}
    )

    def __init__(
        self,
        canvas: InteractiveMapCanvas,
        parent: QWidget | None = None,
    ):
        super().__init__(canvas, parent)
        self.drag_junction_button = QToolButton(self)
        self.drag_junction_button.setText(
            "Drag Junction"
        )
        self.drag_junction_button.setToolTip(
            "Select the green X and drag it to preview the shared junction."
        )
        self.drag_junction_button.setCheckable(True)
        self.drag_junction_button.setChecked(True)
        first_action = (
            self.actions()[0]
            if self.actions()
            else None
        )
        self._drag_junction_action = self.insertWidget(
            first_action,
            self.drag_junction_button,
        )
        if first_action is not None:
            self.insertSeparator(first_action)
        self.drag_junction_button.toggled.connect(
            self._set_junction_drag_mode
        )
        self._set_junction_drag_mode(True)

    @property
    def junction_drag_enabled(self) -> bool:
        return bool(
            self.drag_junction_button.isChecked()
        )

    def set_junction_mode_available(
        self,
        available: bool,
    ) -> None:
        self._drag_junction_action.setVisible(
            bool(available)
        )
        self.drag_junction_button.setVisible(
            bool(available)
        )
        if not available:
            self.drag_junction_button.setChecked(
                False
            )

    def _set_button_checked(
        self,
        checked: bool,
    ) -> None:
        self.drag_junction_button.blockSignals(True)
        self.drag_junction_button.setChecked(
            bool(checked)
        )
        self.drag_junction_button.blockSignals(False)

    def _set_junction_drag_mode(
        self,
        enabled: bool,
    ) -> None:
        if enabled:
            pan_action = self._actions.get("pan")
            zoom_action = self._actions.get("zoom")
            if (
                pan_action is not None
                and pan_action.isChecked()
            ):
                super().pan()
            if (
                zoom_action is not None
                and zoom_action.isChecked()
            ):
                super().zoom()
        self.canvas.set_junction_drag_enabled(
            bool(enabled)
        )

    def pan(self, *args: Any) -> None:
        if self.drag_junction_button.isChecked():
            self._set_button_checked(False)
            self.canvas.set_junction_drag_enabled(
                False
            )
        super().pan(*args)

    def zoom(self, *args: Any) -> None:
        if self.drag_junction_button.isChecked():
            self._set_button_checked(False)
            self.canvas.set_junction_drag_enabled(
                False
            )
        super().zoom(*args)
