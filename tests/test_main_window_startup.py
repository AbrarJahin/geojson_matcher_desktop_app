from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import app.ui.main_window as main_window_module
from app.ui.main_window import MainWindow


class _NoStartupTimer:
    @staticmethod
    def singleShot(_milliseconds: int, _callback) -> None:  # type: ignore[no-untyped-def]
        return None


class _RecordingTimer:
    calls: list[tuple[int, str]] = []

    @classmethod
    def singleShot(cls, milliseconds: int, callback) -> None:  # type: ignore[no-untyped-def]
        cls.calls.append((milliseconds, callback.__name__))


class _FakeScreen:
    def __init__(self, geometry: QRect) -> None:
        self._geometry = geometry

    def availableGeometry(self) -> QRect:
        return QRect(self._geometry)


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_empty_geojson(path: Path) -> None:
    path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")


def test_auto_start_helper_can_still_run_valid_remembered_project_when_called(
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

    # The helper remains available for explicit/internal use, but construction no longer schedules it.
    window._auto_start_last_project()
    assert calls == ["start"]

    # A progress file exists: the helper still rebuilds the pipeline so the core
    # compatibility check can restore only matching decisions.
    state_dir = output / ".road_matcher_state"
    state_dir.mkdir()
    (state_dir / "Alpha__Beta_manual_review_progress.csv").write_text(
        "pair_key,manual_decision\n1||2,yes\n", encoding="utf-8"
    )
    window._auto_start_last_project()
    assert calls == ["start", "start"]

    window.close()


def test_review_completion_starts_finalization_automatically(
    monkeypatch, tmp_path: Path
) -> None:
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
                "safe_rejected": 98,
                "safe_reject_fraction": 0.98,
                "selected": 2,
                "selected_fraction": 0.02,
                "minimum_review_count": 2,
                "maximum_review_count": 2,
                "completed": 2,
                "remaining": 0,
                "threshold": 0.5,
                "global_threshold": 0.5,
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


def test_constructor_schedules_taskbar_fit_but_not_automatic_analysis(monkeypatch) -> None:
    _app()
    _RecordingTimer.calls = []
    monkeypatch.setattr(main_window_module, "QTimer", _RecordingTimer)
    window = MainWindow()

    assert (0, "_fit_to_available_screen") in _RecordingTimer.calls
    assert not any(
        callback == "_auto_start_last_project"
        for _, callback in _RecordingTimer.calls
    )
    window.close()


def test_main_window_initial_geometry_uses_available_work_area(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(main_window_module, "QTimer", _NoStartupTimer)
    available = QRect(100, 50, 1200, 700)
    fake_screen = _FakeScreen(available)
    monkeypatch.setattr(MainWindow, "screen", lambda self: fake_screen)
    window = MainWindow()

    window._fit_to_available_screen()

    assert window.width() <= int(available.width() * 0.92)
    assert window.height() <= int(available.height() * 0.92)
    assert window.width() <= 1000
    assert window.height() <= 760
    window.close()


def test_blank_output_directory_is_rejected_before_path_conversion(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(main_window_module, "QTimer", _NoStartupTimer)
    window = MainWindow()
    window.output_edit.clear()

    with pytest.raises(ValueError, match="Select an output directory"):
        window._config()

    window.close()
