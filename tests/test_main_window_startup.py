from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

import app.ui.main_window as main_window_module
from app.ui.main_window import MainWindow


class _NoStartupTimer:
    @staticmethod
    def singleShot(_milliseconds: int, _callback) -> None:  # type: ignore[no-untyped-def]
        return None


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_empty_geojson(path: Path) -> None:
    path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")


def test_valid_remembered_project_auto_starts_with_or_without_saved_state(
    tmp_path: Path, monkeypatch
) -> None:
    _app()
    monkeypatch.setattr(main_window_module, "QTimer", _NoStartupTimer)
    window = MainWindow()
    assert not hasattr(window, "finalize_button")

    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    output = tmp_path / "output"
    _write_empty_geojson(first)
    _write_empty_geojson(second)
    output.mkdir()

    window.file_1_edit.setText(str(first))
    window.file_2_edit.setText(str(second))
    window.output_edit.setText(str(output))
    calls: list[str] = []
    window._start_analysis = lambda: calls.append("start")  # type: ignore[method-assign]

    # No progress file: the remembered valid project starts from the beginning.
    window._auto_start_last_project()
    assert calls == ["start"]

    # A progress file exists: startup still rebuilds the pipeline so the core
    # compatibility check can restore only matching decisions.
    state_dir = output / ".road_matcher_state"
    state_dir.mkdir()
    (state_dir / "Alpha__Beta_manual_review_progress.csv").write_text(
        "pair_key,manual_decision\n1||2,yes\n", encoding="utf-8"
    )
    window._auto_start_last_project()
    assert calls == ["start", "start"]

    window.close()


def test_review_completion_starts_finalization_automatically(monkeypatch, tmp_path: Path) -> None:
    _app()
    monkeypatch.setattr(main_window_module, "QTimer", _NoStartupTimer)
    window = MainWindow()

    class Pipeline:
        county_1_name = "Alpha"
        county_2_name = "Beta"
        session_path = tmp_path / "state.csv"

        def review_summary(self):
            return {
                "total_candidates": 100,
                "selected": 2,
                "selected_fraction": 0.02,
                "minimum_review_count": 2,
                "maximum_review_count": 30,
                "completed": 2,
                "remaining": 0,
                "threshold": 0.5,
            }

    class CompletedDialog:
        def __init__(self, pipeline, include_basemap, parent):
            self.pipeline = pipeline

        def exec(self):
            return 1

    window.pipeline = Pipeline()  # type: ignore[assignment]
    monkeypatch.setattr(main_window_module, "ManualReviewDialog", CompletedDialog)
    calls: list[str] = []
    window._start_finalization = lambda: calls.append("finalize")  # type: ignore[method-assign]

    window._open_review()
    assert calls == ["finalize"]
    assert not hasattr(window, "finalize_button")
    window.pipeline = None
    window.close()
