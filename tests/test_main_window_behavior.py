from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import app.ui.main_window as main_window_module
from app.core.pipeline import FinalizationResult
from app.ui.main_window import MainWindow


class _NoTimer:
    @staticmethod
    def singleShot(_milliseconds: int, _callback) -> None:  # type: ignore[no-untyped-def]
        return None


class _ImmediateTimer:
    @staticmethod
    def singleShot(_milliseconds: int, callback) -> None:  # type: ignore[no-untyped-def]
        callback()


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _window(monkeypatch) -> MainWindow:  # type: ignore[no-untyped-def]
    _app()
    monkeypatch.setattr(main_window_module, "QTimer", _NoTimer)
    return MainWindow()


def test_config_reflects_current_ui_values(tmp_path: Path, monkeypatch) -> None:
    window = _window(monkeypatch)
    first = tmp_path / "A.geojson"
    second = tmp_path / "B.geojson"
    window.file_1_edit.setText(str(first))
    window.file_2_edit.setText(str(second))
    window.output_edit.setText(str(tmp_path / "out"))
    window.road_id_edit.setText("RID")
    window.buffer_spin.setValue(75.5)
    window.batch_spin.setValue(12)

    config = window._config()

    assert config.county_file_1 == first
    assert config.county_file_2 == second
    assert config.output_dir == tmp_path / "out"
    assert config.road_id_column == "RID"
    assert config.target_crs == "EPSG:26916"
    assert not hasattr(window, "target_crs_edit")
    assert config.buffer_distance_meters == 75.5
    assert config.max_manual_batch_size == 12
    window.close()


def test_set_busy_updates_buttons_progress_and_status(monkeypatch) -> None:
    window = _window(monkeypatch)
    window.pipeline = object()  # type: ignore[assignment]

    window._set_busy(True, "Working")
    assert window.analyze_button.isEnabled() is False
    assert window.review_button.isEnabled() is False
    assert window.quit_button.isEnabled() is False
    assert window.progress.value() == 0
    assert window.status_label.text() == "Working"

    window._set_busy(False, "Done")
    assert window.analyze_button.isEnabled() is True
    assert window.review_button.isEnabled() is True
    assert window.quit_button.isEnabled() is True
    assert window.progress.value() == 100
    assert window.status_label.text() == "Done"
    window.pipeline = None
    window.close()


def test_analysis_stage_clamps_progress_to_zero_through_99(monkeypatch) -> None:
    window = _window(monkeypatch)
    window._analysis_stage(0, 0, "ignored")
    assert window.progress.value() == 0
    window._analysis_stage(3, 4, "ignored")
    assert window.progress.value() == 50
    window._analysis_stage(4, 4, "ignored")
    assert window.progress.value() == 75
    window._analysis_stage(99, 4, "ignored")
    assert window.progress.value() == 99
    assert "99% complete" in window.status_label.text()
    window.close()


def test_append_log_preserves_line_breaks_as_html(monkeypatch) -> None:
    window = _window(monkeypatch)
    window._append_log("first\nsecond")
    text = window.log_edit.toPlainText()
    assert "first" in text
    assert "second" in text
    window.close()


def test_invalid_start_analysis_shows_error_without_creating_thread(monkeypatch) -> None:
    window = _window(monkeypatch)
    window.file_1_edit.clear()
    window.file_2_edit.clear()
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, _title, text: messages.append(str(text)),
    )

    window._start_analysis()

    assert window._thread is None
    assert len(messages) == 1
    window.close()


def test_analyze_resumes_matching_in_memory_pipeline_without_rebuilding(
    monkeypatch, tmp_path: Path
) -> None:
    window = _window(monkeypatch)
    first = tmp_path / "Alpha.geojson"
    second = tmp_path / "Beta.geojson"
    first.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    second.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    window.file_1_edit.setText(str(first))
    window.file_2_edit.setText(str(second))
    window.output_edit.setText(str(tmp_path / "output"))
    config = window._config().normalized()
    active_pipeline = SimpleNamespace(config=config)
    window.pipeline = active_pipeline  # type: ignore[assignment]
    resumed: list[str] = []
    window._continue_current_workflow = (  # type: ignore[method-assign]
        lambda: resumed.append("resume")
    )

    window._start_analysis()

    assert window.pipeline is active_pipeline
    assert window._thread is None
    assert resumed == ["resume"]
    assert "Resuming current review progress" in window.status_label.text()
    window.pipeline = None
    window.close()


def test_input_and_output_browse_reuse_last_directories(
    monkeypatch, tmp_path: Path
) -> None:
    window = _window(monkeypatch)
    # Do not depend on the machine-wide/native QSettings backend. On Windows,
    # its application identity and pre-existing registry values depend on how
    # pytest was launched and can make this unit test order-dependent.
    window._settings = QSettings(
        str(tmp_path / "browse-test.ini"), QSettings.Format.IniFormat
    )
    window._settings.clear()
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()
    existing = input_dir / "existing.geojson"
    existing.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    selected = input_dir / "selected.geojson"
    starts: list[Path] = []

    def select_file(_parent, _title, start, _filter):  # type: ignore[no-untyped-def]
        starts.append(Path(start))
        return str(selected), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", select_file)
    window.file_1_edit.setText(str(existing))
    window._select_file(window.file_1_edit)
    assert starts == [input_dir]
    assert window.file_1_edit.text() == str(selected)

    window.file_1_edit.clear()
    window.file_2_edit.clear()
    window._select_file(window.file_2_edit)
    assert starts[-1] == input_dir

    output_starts: list[Path] = []
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda _parent, _title, start: output_starts.append(Path(start))
        or str(output_dir),
    )
    window.output_edit.setText(str(output_dir))
    window._select_output_dir()
    assert output_starts == [output_dir]
    assert window.output_edit.text() == str(output_dir)
    window.close()


def test_dialog_directory_helpers_cover_file_directory_and_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    window = _window(monkeypatch)
    settings = QSettings(
        str(tmp_path / "dialog-test.ini"), QSettings.Format.IniFormat
    )
    settings.clear()
    window._settings = settings
    directory = tmp_path / "inputs"
    directory.mkdir()
    file_path = directory / "roads.geojson"
    file_path.write_text("{}", encoding="utf-8")

    assert window._existing_dialog_directory("") is None
    assert window._existing_dialog_directory(directory) == directory
    assert window._existing_dialog_directory(file_path) == directory
    assert window._existing_dialog_directory(directory / "new.geojson") == directory
    assert window._existing_dialog_directory(tmp_path / "missing" / "file.json") is None

    settings.setValue("paths/last_input_dir", str(directory))
    window.file_1_edit.clear()
    window.file_2_edit.clear()
    assert window._input_dialog_start_directory(window.file_2_edit) == directory
    window.close()


def test_cancelled_browse_does_not_change_paths_or_settings(
    monkeypatch, tmp_path: Path
) -> None:
    window = _window(monkeypatch)
    settings = QSettings(
        str(tmp_path / "cancel-test.ini"), QSettings.Format.IniFormat
    )
    settings.clear()
    window._settings = settings
    original_file = str(tmp_path / "original.geojson")
    original_output = str(tmp_path / "output")
    window.file_1_edit.setText(original_file)
    window.output_edit.setText(original_output)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")

    window._select_file(window.file_1_edit)
    window._select_output_dir()

    assert window.file_1_edit.text() == original_file
    assert window.output_edit.text() == original_output
    assert settings.value("paths/last_input_dir") is None
    assert settings.value("paths/last_output_dir") is None
    window.close()


def test_analysis_completion_reports_recovered_session_and_routes_next_step(
    monkeypatch
) -> None:
    window = _window(monkeypatch)
    warnings: list[tuple[str, str]] = []
    information: list[str] = []
    routed: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((str(title), str(text))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, _text: information.append(str(title)),
    )
    window._run_after_background_thread = (  # type: ignore[method-assign]
        lambda callback: routed.append(callback.__name__)
    )
    pipeline = SimpleNamespace(
        set_logger=lambda _logger: None,
        loaded_session_path=None,
        session_recovery_warning="working GeoJSON files are missing",
        review_summary=lambda: {
            "total_candidates": 10,
            "safe_rejected": 8,
            "safe_reject_fraction": 0.8,
            "selected": 2,
            "global_threshold": 0.5,
        },
        junction_review_summary=lambda: {
            "round": 1,
            "selected": 2,
            "completed": 0,
            "remaining": 2,
            "deferred": 1,
        },
    )

    window._analysis_completed(pipeline)  # type: ignore[arg-type]

    assert warnings == [
        ("Saved progress unavailable", "working GeoJSON files are missing")
    ]
    assert information == ["Processing complete"]
    assert routed == ["_continue_current_workflow"]
    assert "No saved session was loaded" in window.summary_label.text()
    assert window.review_button.isEnabled() is True
    window.pipeline = None
    window.close()


def test_continue_workflow_routes_pending_committed_and_finished_states(
    monkeypatch,
) -> None:
    window = _window(monkeypatch)
    calls: list[str] = []
    window._open_review = lambda: calls.append("review")  # type: ignore[method-assign]
    window._start_junction_round_completion = (  # type: ignore[method-assign]
        lambda: calls.append("commit")
    )
    window._start_finalization = (  # type: ignore[method-assign]
        lambda: calls.append("finalize")
    )

    for summary, expected in [
        ({"remaining": 1, "selected": 1}, "review"),
        ({"remaining": 0, "selected": 1}, "commit"),
        ({"remaining": 0, "selected": 0}, "finalize"),
    ]:
        window.pipeline = SimpleNamespace(  # type: ignore[assignment]
            junction_review_summary=lambda value=summary: value
        )
        window._continue_current_workflow()
        assert calls[-1] == expected

    window.pipeline = None
    window._continue_current_workflow()
    assert calls == ["review", "commit", "finalize"]
    window.close()


def test_task_failed_resets_ui_and_reports_error(monkeypatch) -> None:
    window = _window(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, text: messages.append(f"{title}:{text}"),
    )

    window._task_failed("Traceback: boom")

    assert "Traceback: boom" in window.log_edit.toPlainText()
    assert "Operation failed" in window.status_label.text()
    assert messages and messages[0].startswith("Operation failed:")
    window.close()


def test_background_thread_finished_clears_worker_references(monkeypatch) -> None:
    window = _window(monkeypatch)
    window._thread = object()  # type: ignore[assignment]
    window._worker = object()
    window._background_thread_finished()
    assert window._thread is None
    assert window._worker is None
    window.close()


def test_next_workflow_step_waits_for_background_thread_exit(monkeypatch) -> None:
    window = _window(monkeypatch)
    monkeypatch.setattr(main_window_module, "QTimer", _ImmediateTimer)

    class RunningThread:
        @staticmethod
        def isRunning() -> bool:
            return True

    window._thread = RunningThread()  # type: ignore[assignment]
    window._worker = object()
    calls: list[str] = []

    window._run_after_background_thread(lambda: calls.append("next"))
    assert calls == []

    window._background_thread_finished()
    assert calls == ["next"]
    assert window._thread is None
    assert window._worker is None
    window.close()


def test_save_session_before_exit_success_and_failure(monkeypatch, tmp_path: Path) -> None:
    window = _window(monkeypatch)

    class GoodPipeline:
        def save_session(self, *, force: bool, reason: str):
            assert force is True
            assert reason == "application quit"
            return tmp_path / "session.csv"

    window.pipeline = GoodPipeline()  # type: ignore[assignment]
    assert window._save_session_before_exit() is True

    class BadPipeline:
        def save_session(self, *, force: bool, reason: str):
            raise PermissionError("locked")

    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, _title, text: messages.append(str(text)),
    )
    window.pipeline = BadPipeline()  # type: ignore[assignment]
    assert window._save_session_before_exit() is False
    assert "locked" in messages[0]
    window.pipeline = None
    window.close()


def test_open_output_folder_creates_directory_and_uses_desktop_services(
    monkeypatch, tmp_path: Path
) -> None:
    window = _window(monkeypatch)
    output = tmp_path / "new" / "output"
    window.output_edit.setText(str(output))
    urls: list[str] = []
    monkeypatch.setattr(
        main_window_module.QDesktopServices,
        "openUrl",
        lambda url: urls.append(url.toLocalFile()) or True,
    )

    window._open_output_folder()

    assert output.is_dir()
    assert [Path(path).resolve() for path in urls] == [output.resolve()]
    window.close()


def test_start_junction_round_completion_warns_when_pending(monkeypatch) -> None:
    window = _window(monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(str(text)),
    )
    window.pipeline = SimpleNamespace(
        junction_review_summary=lambda: {"remaining": 2, "selected": 3, "round": 1}
    )  # type: ignore[assignment]

    window._start_junction_round_completion()

    assert warnings == ["2 junction(s) remain unanswered in this round."]
    assert window._thread is None
    window.pipeline = None
    window.close()


def test_start_junction_round_completion_finalizes_when_no_junctions(monkeypatch) -> None:
    window = _window(monkeypatch)
    window.pipeline = SimpleNamespace(
        junction_review_summary=lambda: {"remaining": 0, "selected": 0, "round": 2}
    )  # type: ignore[assignment]
    calls: list[str] = []
    window._start_finalization = lambda: calls.append("finalize")  # type: ignore[method-assign]

    window._start_junction_round_completion()

    assert calls == ["finalize"]
    window.pipeline = None
    window.close()


def test_start_finalization_refuses_uncommitted_junction_round(monkeypatch) -> None:
    window = _window(monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(str(text)),
    )
    window.pipeline = SimpleNamespace(
        junction_review_summary=lambda: {"remaining": 0, "selected": 1}
    )  # type: ignore[assignment]

    window._start_finalization()

    assert warnings and "fully reviewed and committed" in warnings[0]
    assert window._thread is None
    window.pipeline = None
    window.close()


def test_finalization_completed_updates_summary_and_message(monkeypatch, tmp_path: Path) -> None:
    window = _window(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, _text: messages.append(str(title)),
    )
    result = FinalizationResult(
        county_1_output=tmp_path / "a.json",
        county_2_output=tmp_path / "b.json",
        final_decisions_csv=tmp_path / "decisions.csv",
        decision_audit_csv=tmp_path / "audit.csv",
        connection_audit_csv=tmp_path / "connections.csv",
        accepted_pairs=7,
        rejected_pairs=11,
    )

    window._finalization_completed(result)

    assert "Accepted pairs: 7" in window.summary_label.text()
    assert "Rejected pairs: 11" in window.summary_label.text()
    assert window.status_label.text() == "Final outputs created successfully."
    assert messages == ["Road matching complete"]
    window.close()
