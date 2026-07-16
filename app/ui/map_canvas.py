from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import ScalarFormatter
from PySide6.QtWidgets import QWidget
from shapely.geometry import Point, box

try:
    import contextily as ctx
    from xyzservices import TileProvider
except Exception:  # The map still works without online tiles.
    ctx = None
    TileProvider = None


class InteractiveMapCanvas(FigureCanvasQTAgg):
    """Matplotlib map with wheel zoom and direct left-button drag panning."""

    def __init__(self, parent: QWidget | None = None):
        self.figure = Figure(figsize=(9, 7), tight_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self.setParent(parent)
        self._drag_state: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None
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

    def draw_pair(self, pipeline: Any, plan_row: Any, include_basemap: bool = True) -> None:
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

        if include_basemap and ctx is not None and TileProvider is not None:
            provider = TileProvider(
                name="GoogleRoadmap",
                url="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
                attribution="Google",
                max_zoom=20,
            )
            try:
                ctx.add_basemap(
                    ax,
                    source=provider,
                    crs=pipeline.config.target_crs,
                    alpha=0.40,
                    attribution_size=6,
                    reset_extent=False,
                    zorder=0,
                )
            except Exception as first_error:
                pipeline.log(f"Google basemap unavailable; trying Esri fallback: {first_error}")
                try:
                    ctx.add_basemap(
                        ax,
                        source=ctx.providers.Esri.WorldStreetMap,
                        crs=pipeline.config.target_crs,
                        alpha=0.40,
                        attribution_size=6,
                        reset_extent=False,
                        zorder=0,
                    )
                except Exception as second_error:
                    pipeline.log(f"Basemap unavailable; continuing with vector roads: {second_error}")

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
        self.draw_idle()


class MapNavigationToolbar(NavigationToolbar2QT):
    toolitems = tuple(
        item for item in NavigationToolbar2QT.toolitems
        if item[0] in {"Home", "Back", "Forward", "Pan", "Zoom", "Save"}
    )
