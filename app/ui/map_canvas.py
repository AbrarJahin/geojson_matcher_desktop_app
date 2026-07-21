from __future__ import annotations

import inspect
from collections import OrderedDict
from typing import Any

import geopandas as gpd
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import ScalarFormatter
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QWidget
from pyproj import Transformer
from shapely.geometry import Point, box

try:
    import contextily as ctx
    from xyzservices import TileProvider
except Exception:  # The map still works without online tiles.
    ctx = None
    TileProvider = None


class _BasemapSignals(QObject):
    finished = Signal(int, object, object, str)


class _BasemapTask(QRunnable):
    """Download and reproject map tiles without blocking Qt's GUI thread."""

    def __init__(
        self,
        token: int,
        bounds: tuple[float, float, float, float],
        target_crs: str,
    ) -> None:
        super().__init__()
        self.token = token
        self.bounds = bounds
        self.target_crs = target_crs
        self.signals = _BasemapSignals()

    @staticmethod
    def _provider_candidates() -> list[Any]:
        providers: list[Any] = []
        if TileProvider is not None:
            providers.append(
                TileProvider(
                    name="GoogleRoadmap",
                    url="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
                    attribution="Google",
                    max_zoom=20,
                )
            )
        if ctx is not None:
            try:
                providers.append(ctx.providers.Esri.WorldStreetMap)
            except Exception:
                pass
        return providers

    @Slot()
    def run(self) -> None:
        if ctx is None:
            self.signals.finished.emit(
                self.token, None, None, "contextily is unavailable; vector roads are still displayed."
            )
            return

        try:
            transformer = Transformer.from_crs(
                self.target_crs, "EPSG:3857", always_xy=True
            )
            west, south, east, north = transformer.transform_bounds(
                *self.bounds, densify_pts=21
            )
        except Exception as exc:
            self.signals.finished.emit(
                self.token, None, None, f"Could not transform basemap bounds: {exc}"
            )
            return

        errors: list[str] = []
        for provider in self._provider_candidates():
            try:
                kwargs: dict[str, Any] = {
                    "zoom": "auto",
                    "source": provider,
                    "ll": False,
                    "wait": 0,
                    "max_retries": 0,
                    "n_connections": 1,
                    "use_cache": True,
                }
                # Contextily 1.7+ exposes a request timeout. Keep compatibility
                # with 1.6.x, which does not accept this keyword.
                if "timeout" in inspect.signature(ctx.bounds2img).parameters:
                    kwargs["timeout"] = 5

                image, extent = ctx.bounds2img(
                    west, south, east, north, **kwargs
                )
                image, extent = ctx.warp_tiles(
                    image, extent, t_crs=self.target_crs
                )
                self.signals.finished.emit(self.token, image, tuple(extent), "")
                return
            except Exception as exc:
                provider_name = getattr(provider, "name", str(provider))
                errors.append(f"{provider_name}: {exc}")

        message = "Basemap unavailable; continuing with vector roads."
        if errors:
            message += " " + " | ".join(errors)
        self.signals.finished.emit(self.token, None, None, message)


class InteractiveMapCanvas(FigureCanvasQTAgg):
    """Matplotlib map with non-blocking basemap loading, zoom, and panning."""

    _BASEMAP_CACHE_LIMIT = 24

    def __init__(self, parent: QWidget | None = None):
        self.figure = Figure(figsize=(9, 7), tight_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self.setParent(parent)
        self._drag_state: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None
        self._draw_token = 0
        self._pending_basemap: tuple[int, tuple[float, float, float, float], str, Any] | None = None
        self._basemap_cache: OrderedDict[tuple[Any, ...], tuple[Any, tuple[float, ...]]] = OrderedDict()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._basemap_timer = QTimer(self)
        self._basemap_timer.setSingleShot(True)
        self._basemap_timer.setInterval(300)
        self._basemap_timer.timeout.connect(self._start_pending_basemap)

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
        rel_x = (event.xdata - x_min) / (x_max - x_min)
        rel_y = (event.ydata - y_min) / (y_max - y_min)
        ax.set_xlim(event.xdata - new_width * rel_x, event.xdata + new_width * (1 - rel_x))
        ax.set_ylim(event.ydata - new_height * rel_y, event.ydata + new_height * (1 - rel_y))
        self.draw_idle()

    def _on_press(self, event: Any) -> None:
        if event.button != 1 or event.xdata is None or event.ydata is None or self._toolbar_is_active():
            return
        self._drag_state = (event.xdata, event.ydata, self.axes.get_xlim(), self.axes.get_ylim())

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
            ax.plot(x_values, y_values, label=label if index == 0 else None, **kwargs)

    @staticmethod
    def _visible_roads(layer: gpd.GeoDataFrame, bounds: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
        viewport = box(*bounds)
        try:
            positions = list(layer.sindex.intersection(viewport.bounds))
            subset = layer.iloc[positions].copy()
        except Exception:
            subset = layer.copy()
        if subset.empty:
            return subset
        subset = subset.loc[subset.geometry.notna() & ~subset.geometry.is_empty].copy()
        if subset.empty:
            return subset
        return subset.loc[subset.geometry.intersects(viewport)].copy()

    @staticmethod
    def _cache_key(
        bounds: tuple[float, float, float, float], target_crs: str
    ) -> tuple[Any, ...]:
        # Meter-level rounding avoids duplicate requests for effectively
        # identical views while keeping the image aligned with the roads.
        return (str(target_crs),) + tuple(round(float(value), 1) for value in bounds)

    def _schedule_basemap(
        self,
        token: int,
        bounds: tuple[float, float, float, float],
        target_crs: str,
        pipeline: Any,
    ) -> None:
        key = self._cache_key(bounds, target_crs)
        cached = self._basemap_cache.get(key)
        if cached is not None:
            image, extent = cached
            self._apply_basemap(token, image, extent, "", pipeline, key)
            return

        self._pending_basemap = (token, bounds, target_crs, pipeline)
        # Restarting the timer means rapid Yes/No clicks do not create a queue
        # of obsolete network requests. Only the pair where the user pauses
        # briefly requests tiles.
        self._basemap_timer.start()

    @Slot()
    def _start_pending_basemap(self) -> None:
        pending = self._pending_basemap
        self._pending_basemap = None
        if pending is None:
            return
        token, bounds, target_crs, pipeline = pending
        if token != self._draw_token:
            return
        task = _BasemapTask(token, bounds, target_crs)
        key = self._cache_key(bounds, target_crs)
        task.signals.finished.connect(
            lambda result_token, image, extent, error: self._apply_basemap(
                result_token, image, extent, error, pipeline, key
            )
        )
        self._thread_pool.start(task)

    def _apply_basemap(
        self,
        token: int,
        image: Any,
        extent: Any,
        error: str,
        pipeline: Any,
        cache_key: tuple[Any, ...],
    ) -> None:
        if error:
            pipeline.log(error)
        if token != self._draw_token or image is None or extent is None:
            return

        self._basemap_cache[cache_key] = (image, tuple(extent))
        self._basemap_cache.move_to_end(cache_key)
        while len(self._basemap_cache) > self._BASEMAP_CACHE_LIMIT:
            self._basemap_cache.popitem(last=False)

        current_xlim = self.axes.get_xlim()
        current_ylim = self.axes.get_ylim()
        self.axes.imshow(
            image,
            extent=extent,
            interpolation="bilinear",
            zorder=0,
        )
        self.axes.set_xlim(*current_xlim)
        self.axes.set_ylim(*current_ylim)
        self.draw_idle()

    def draw_pair(self, pipeline: Any, plan_row: Any, include_basemap: bool = True) -> None:
        self._draw_token += 1
        token = self._draw_token
        self._basemap_timer.stop()
        self._pending_basemap = None

        pair = pipeline.pair_features(plan_row["county_1_id"], plan_row["county_2_id"])
        geometry_1 = pair["geometry_county1"]
        geometry_2 = pair["geometry_county2"]
        contact_1 = pair["county_1_contact_point"]
        contact_2 = pair["county_2_contact_point"]
        midpoint = Point((contact_1.x + contact_2.x) / 2.0, (contact_1.y + contact_2.y) / 2.0)

        bounds = gpd.GeoSeries(
            [geometry_1, geometry_2, contact_1, contact_2], crs=pipeline.config.target_crs
        ).total_bounds
        min_x, min_y, max_x, max_y = [float(value) for value in bounds]
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        padding_x = max(width * 0.20, 20.0)
        padding_y = max(height * 0.20, 20.0)
        side = max(width + 2 * padding_x, height + 2 * padding_y)
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        half = side / 2
        view = (center_x - half, center_y - half, center_x + half, center_y + half)

        ax = self.axes
        ax.clear()
        ax.set_xlim(view[0], view[2])
        ax.set_ylim(view[1], view[3])
        ax.set_aspect("equal")

        for layer in pipeline.context_layers():
            for road_geometry in self._visible_roads(layer, view).geometry:
                self._plot_geometry(
                    ax,
                    road_geometry,
                    color="#57450F",
                    linewidth=1.25,
                    alpha=0.95,
                    zorder=2,
                )

        name_1 = str(pair.get("full_road_label_county1") or pair.get("road_name_county1") or plan_row["county_1_id"])
        name_2 = str(pair.get("full_road_label_county2") or pair.get("road_name_county2") or plan_row["county_2_id"])
        self._plot_geometry(
            ax, geometry_1, color="blue", linewidth=4.0, alpha=0.50,
            label=f"{pipeline.county_1_name}: {name_1}", zorder=5,
        )
        self._plot_geometry(
            ax, geometry_2, color="orange", linewidth=4.0, alpha=0.50,
            label=f"{pipeline.county_2_name}: {name_2}", zorder=5,
        )
        ax.plot(
            [contact_1.x, contact_2.x], [contact_1.y, contact_2.y],
            color="black", linewidth=1.7, linestyle="--", alpha=0.90,
            label="Current gap", zorder=6,
        )
        ax.scatter(
            contact_1.x, contact_1.y, s=65, color="red", alpha=0.50,
            edgecolor="black", linewidth=0.8,
            label=f"{pipeline.county_1_name} contact", zorder=7,
        )
        ax.scatter(
            contact_2.x, contact_2.y, s=65, color="green", alpha=0.50,
            edgecolor="black", linewidth=0.8,
            label=f"{pipeline.county_2_name} contact", zorder=7,
        )
        ax.scatter(
            midpoint.x, midpoint.y, s=150, marker="X", color="limegreen",
            edgecolor="black", linewidth=1.0, label="Proposed shared midpoint", zorder=8,
        )

        ax.set_title(
            f"Possible connected roads — {pipeline.county_1_name} {plan_row['county_1_id']} / "
            f"{pipeline.county_2_name} {plan_row['county_2_id']}"
        )
        ax.legend(loc="upper right", fontsize=9, markerscale=1.1)
        ax.grid(True, alpha=0.50)
        ax.set_xlabel("X coordinate (meters)")
        ax.set_ylabel("Y coordinate (meters)")
        x_formatter = ScalarFormatter(useOffset=False)
        y_formatter = ScalarFormatter(useOffset=False)
        x_formatter.set_scientific(False)
        y_formatter.set_scientific(False)
        ax.xaxis.set_major_formatter(x_formatter)
        ax.yaxis.set_major_formatter(y_formatter)
        ax.set_xlim(view[0], view[2])
        ax.set_ylim(view[1], view[3])
        ax.set_aspect("equal")

        # Vector roads are painted immediately. Network tile work is delayed
        # and executed on a worker thread so Yes/No clicks remain responsive.
        self.draw_idle()
        if include_basemap and ctx is not None:
            self._schedule_basemap(token, view, pipeline.config.target_crs, pipeline)


class MapNavigationToolbar(NavigationToolbar2QT):
    toolitems = tuple(
        item for item in NavigationToolbar2QT.toolitems
        if item[0] in {"Home", "Back", "Forward", "Pan", "Zoom", "Save"}
    )
