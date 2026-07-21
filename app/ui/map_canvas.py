from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any

import geopandas as gpd
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import ScalarFormatter
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QImage, QPainter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QWidget
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.ops import transform as shapely_transform

WEB_MERCATOR_CRS = "EPSG:3857"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
WEB_MERCATOR_WORLD = WEB_MERCATOR_HALF_WORLD * 2.0
TILE_SIZE = 256
MAX_TILE_REQUESTS = 16


class InteractiveMapCanvas(FigureCanvasQTAgg):
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
        self._draw_token = 0
        self._shutting_down = False
        self._transformers: dict[str, Transformer] = {}

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
        if (
            event.button != 1
            or event.xdata is None
            or event.ydata is None
            or self._toolbar_is_active()
        ):
            return
        self._drag_state = (
            event.xdata,
            event.ydata,
            self.axes.get_xlim(),
            self.axes.get_ylim(),
        )

    def _on_motion(self, event: Any) -> None:
        if self._drag_state is None or event.xdata is None or event.ydata is None:
            return
        start_x, start_y, x_limits, y_limits = self._drag_state
        dx = event.xdata - start_x
        dy = event.ydata - start_y
        self.axes.set_xlim(x_limits[0] - dx, x_limits[1] - dx)
        self.axes.set_ylim(y_limits[0] - dy, y_limits[1] - dy)
        self.draw_idle()

    def _on_release(self, _: Any) -> None:
        self._drag_state = None

    @staticmethod
    def _plot_geometry(ax: Any, geometry: Any, **kwargs: Any) -> None:
        if geometry is None or geometry.is_empty:
            return
        if geometry.geom_type == "LineString":
            parts = [geometry]
        elif geometry.geom_type == "MultiLineString":
            parts = list(geometry.geoms)
        else:
            return
        label = kwargs.pop("label", None)
        for index, part in enumerate(parts):
            x_values, y_values = part.xy
            ax.plot(
                x_values,
                y_values,
                label=label if index == 0 else None,
                **kwargs,
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
        valid = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
        if not valid:
            raise ValueError("No drawable geometry was available for the selected road pair.")
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
        return center_x - half, center_y - half, center_x + half, center_y + half

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
        return tile_x / (2**zoom) * WEB_MERCATOR_WORLD - WEB_MERCATOR_HALF_WORLD

    @staticmethod
    def _meters_y(tile_y: float, zoom: int) -> float:
        return WEB_MERCATOR_HALF_WORLD - tile_y / (2**zoom) * WEB_MERCATOR_WORLD

    def _tile_grid(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int, int]:
        west, south, east, north = bounds
        pixel_width = max(int(self.width()), 800)
        desired_mpp = max((east - west) / pixel_width, 0.01)
        initial_zoom = int(
            math.floor(
                math.log2(WEB_MERCATOR_WORLD / (TILE_SIZE * desired_mpp))
            )
        )
        zoom = max(0, min(initial_zoom, 19))

        while True:
            tile_count = 2**zoom
            x_min = max(0, min(tile_count - 1, int(math.floor(self._tile_x(west, zoom)))))
            x_max = max(0, min(tile_count - 1, int(math.floor(self._tile_x(east, zoom)))))
            y_min = max(0, min(tile_count - 1, int(math.floor(self._tile_y(north, zoom)))))
            y_max = max(0, min(tile_count - 1, int(math.floor(self._tile_y(south, zoom)))))
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

        zoom, x_min, y_min, x_max, y_max = self._tile_grid(bounds)
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

        user_agent = b"RoadMatcherDesktop/1.1 (manual road-pair research review)"
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
                batch["errors"].append("A downloaded basemap tile was not a valid image.")
            else:
                batch["images"][(context["tile_x"], context["tile_y"])] = image
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
            errors = sorted(set(str(value) for value in batch["errors"] if value))
            detail = f" Details: {' | '.join(errors[:3])}" if errors else ""
            pipeline.log("Online basemap unavailable; vector roads remain visible." + detail)
            return

        x_min = int(batch["x_min"])
        x_max = int(batch["x_max"])
        y_min = int(batch["y_min"])
        y_max = int(batch["y_max"])
        zoom = int(batch["zoom"])
        width = (x_max - x_min + 1) * TILE_SIZE
        height = (y_max - y_min + 1) * TILE_SIZE
        mosaic = QImage(width, height, QImage.Format.Format_RGBA8888)
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

        rgba = mosaic.convertToFormat(QImage.Format.Format_RGBA8888)
        bytes_per_line = rgba.bytesPerLine()
        raw = np.frombuffer(
            rgba.constBits(), dtype=np.uint8, count=rgba.sizeInBytes()
        ).reshape((rgba.height(), bytes_per_line))
        array = raw[:, : rgba.width() * 4].reshape(
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
        self, token: int, image: np.ndarray, extent: tuple[float, ...]
    ) -> None:
        if token != self._draw_token or self._shutting_down:
            return
        current_xlim = self.axes.get_xlim()
        current_ylim = self.axes.get_ylim()
        self.axes.imshow(
            image,
            extent=extent,
            interpolation="bilinear",
            origin="upper",
            zorder=0,
        )
        self.axes.text(
            0.005,
            0.005,
            "© OpenStreetMap contributors",
            transform=self.axes.transAxes,
            fontsize=7,
            alpha=0.75,
            zorder=20,
        )
        self.axes.set_xlim(*current_xlim)
        self.axes.set_ylim(*current_ylim)
        self.draw_idle()

    def shutdown(self) -> None:
        """Abort network activity without waiting for any background thread."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._draw_token += 1
        self._cancel_tile_requests()
        self._basemap_cache.clear()

    def draw_pair(
        self, pipeline: Any, plan_row: Any, include_basemap: bool = True
    ) -> None:
        if self._shutting_down:
            return
        self._draw_token += 1
        token = self._draw_token
        self._cancel_tile_requests()

        pair = pipeline.pair_features(
            plan_row["county_1_id"], plan_row["county_2_id"]
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
            [geometry_1, geometry_2, contact_1, contact_2]
        )
        source_crs = pipeline.config.target_crs
        geometry_1_web = self._to_web_mercator(geometry_1, source_crs)
        geometry_2_web = self._to_web_mercator(geometry_2, source_crs)
        contact_1_web = self._to_web_mercator(contact_1, source_crs)
        contact_2_web = self._to_web_mercator(contact_2, source_crs)
        midpoint_web = self._to_web_mercator(midpoint, source_crs)
        web_view = self._square_bounds(
            [geometry_1_web, geometry_2_web, contact_1_web, contact_2_web],
            minimum_padding=20.0,
        )

        ax = self.axes
        ax.clear()
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        for layer in pipeline.context_layers():
            visible = self._visible_roads(layer, target_view)
            for road_geometry in visible.geometry:
                web_geometry = self._to_web_mercator(road_geometry, source_crs)
                self._plot_geometry(
                    ax,
                    web_geometry,
                    color="#57450F",
                    linewidth=1.25,
                    alpha=0.95,
                    zorder=2,
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
        self._plot_geometry(
            ax,
            geometry_1_web,
            color="blue",
            linewidth=4.0,
            alpha=0.50,
            label=f"{pipeline.county_1_name}: {name_1}",
            zorder=5,
        )
        self._plot_geometry(
            ax,
            geometry_2_web,
            color="orange",
            linewidth=4.0,
            alpha=0.50,
            label=f"{pipeline.county_2_name}: {name_2}",
            zorder=5,
        )
        ax.plot(
            [contact_1_web.x, contact_2_web.x],
            [contact_1_web.y, contact_2_web.y],
            color="black",
            linewidth=1.7,
            linestyle="--",
            alpha=0.90,
            label="Current gap",
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
            label=f"{pipeline.county_1_name} contact",
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
            label=f"{pipeline.county_2_name} contact",
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
            label="Proposed shared midpoint",
            zorder=8,
        )

        ax.set_title(
            f"Possible connected roads — {pipeline.county_1_name} {plan_row['county_1_id']} / "
            f"{pipeline.county_2_name} {plan_row['county_2_id']}"
        )
        ax.legend(loc="upper right", fontsize=9, markerscale=1.1)
        ax.grid(True, alpha=0.50)
        ax.set_xlabel("Web Mercator X coordinate (meters)")
        ax.set_ylabel("Web Mercator Y coordinate (meters)")
        x_formatter = ScalarFormatter(useOffset=False)
        y_formatter = ScalarFormatter(useOffset=False)
        x_formatter.set_scientific(False)
        y_formatter.set_scientific(False)
        ax.xaxis.set_major_formatter(x_formatter)
        ax.yaxis.set_major_formatter(y_formatter)
        ax.set_xlim(web_view[0], web_view[2])
        ax.set_ylim(web_view[1], web_view[3])
        ax.set_aspect("equal")

        self.draw_idle()
        if include_basemap:
            self._schedule_basemap(token, web_view, pipeline)


class MapNavigationToolbar(NavigationToolbar2QT):
    toolitems = tuple(
        item
        for item in NavigationToolbar2QT.toolitems
        if item[0] in {"Home", "Back", "Forward", "Pan", "Zoom", "Save"}
    )
