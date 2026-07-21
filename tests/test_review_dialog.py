from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
from PySide6.QtWidgets import QApplication
from shapely.geometry import LineString, Point

from app.ui.map_canvas import InteractiveMapCanvas
from app.ui.review_dialog import ManualReviewDialog


class FakePipeline:
    def __init__(self, tmp_path: Path) -> None:
        self.config = SimpleNamespace(target_crs="EPSG:26916")
        self.county_1_name = "Alpha"
        self.county_2_name = "Beta"
        self.optimized_threshold = 0.5
        self.saved_calls = 0
        self.session_file = tmp_path / "session.csv"
        self.logs: list[str] = []
        self._decisions = {"1||101": pd.NA, "2||102": pd.NA}
        self._rows = pd.DataFrame(
            [
                {
                    "pair_key": "1||101",
                    "county_1_id": "1",
                    "county_2_id": "101",
                    "manual_pair_number": 1,
                    "manual_batch_id": 1,
                    "manual_decision": pd.NA,
                    "geometric_valid_pair_probability": 0.9,
                    "textual_valid_pair_probability": 0.1,
                    "probablity": 0.55,
                    "manual_review_selected_from": "test",
                },
                {
                    "pair_key": "2||102",
                    "county_1_id": "2",
                    "county_2_id": "102",
                    "manual_pair_number": 2,
                    "manual_batch_id": 1,
                    "manual_decision": pd.NA,
                    "geometric_valid_pair_probability": 0.2,
                    "textual_valid_pair_probability": 0.8,
                    "probablity": 0.45,
                    "manual_review_selected_from": "test",
                },
            ]
        )
        self._line_1 = LineString([(500000.0, 4420000.0), (500100.0, 4420000.0)])
        self._line_2 = LineString([(500105.0, 4420005.0), (500205.0, 4420005.0)])
        self._context = (
            gpd.GeoDataFrame(
                {"geometry": [self._line_1]}, geometry="geometry", crs="EPSG:26916"
            ),
            gpd.GeoDataFrame(
                {"geometry": [self._line_2]}, geometry="geometry", crs="EPSG:26916"
            ),
        )

    def review_rows(self, include_completed: bool = True) -> pd.DataFrame:
        rows = self._rows.copy()
        rows["manual_decision"] = rows["pair_key"].map(self._decisions)
        if not include_completed:
            rows = rows.loc[rows["manual_decision"].isna()].copy()
        return rows.reset_index(drop=True)

    def pair_features(self, county_1_id: str, county_2_id: str) -> pd.Series:
        return pd.Series(
            {
                "geometry_county1": self._line_1,
                "geometry_county2": self._line_2,
                "county_1_contact_point": Point(500100.0, 4420000.0),
                "county_2_contact_point": Point(500105.0, 4420005.0),
                "full_road_label_county1": f"Alpha {county_1_id}",
                "full_road_label_county2": f"Beta {county_2_id}",
            }
        )

    def context_layers(self):
        return self._context

    def record_manual_decision(self, pair_key: str, decision: str) -> None:
        self._decisions[pair_key] = decision

    def save_session(self, *, force: bool, reason: str):
        self.saved_calls += 1
        self.session_file.write_text(
            "pair_key,manual_decision\n"
            + "\n".join(f"{key},{value}" for key, value in self._decisions.items()),
            encoding="utf-8",
        )
        return self.session_file

    def log(self, message: str) -> None:
        self.logs.append(str(message))


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_yes_no_and_review_close_stay_in_ram_until_application_quit(tmp_path: Path) -> None:
    app = _app()
    pipeline = FakePipeline(tmp_path)
    dialog = ManualReviewDialog(pipeline, include_basemap=False)

    dialog._record("yes")
    app.processEvents()
    assert pipeline._decisions["1||101"] == "yes"
    assert pipeline.saved_calls == 0
    assert not pipeline.session_file.exists()

    dialog.accept()
    app.processEvents()
    assert pipeline.saved_calls == 0
    assert not pipeline.session_file.exists()

    # The main application close/quit flow is the only durable-save owner.
    pipeline.save_session(force=True, reason="application quit")
    assert pipeline.saved_calls == 1
    assert pipeline.session_file.exists()


def test_map_canvas_repeated_redraw_and_shutdown_without_worker_threads(tmp_path: Path) -> None:
    app = _app()
    pipeline = FakePipeline(tmp_path)
    canvas = InteractiveMapCanvas()
    rows = pipeline.review_rows()

    for index in range(30):
        canvas.draw_pair(pipeline, rows.iloc[index % len(rows)], include_basemap=False)
        app.processEvents()

    assert not hasattr(canvas, "_thread_pool")
    canvas.shutdown()
    app.processEvents()


def test_online_tile_requests_can_be_aborted_during_close(tmp_path: Path) -> None:
    app = _app()
    pipeline = FakePipeline(tmp_path)
    canvas = InteractiveMapCanvas()
    canvas.draw_pair(pipeline, pipeline.review_rows().iloc[0], include_basemap=True)
    canvas.shutdown()
    app.processEvents()
    assert canvas._shutting_down is True
    assert canvas._reply_context == {}
