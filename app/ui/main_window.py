from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QThread, QTimer, QUrl, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.pipeline import FinalizationResult, PipelineConfig, RoadMatchingPipeline
from app.ui.review_dialog import ManualReviewDialog
from app.workers.tasks import AnalysisWorker, FinalizationWorker, JunctionRoundWorker

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        application = QApplication.instance()
        if application is not None and not application.windowIcon().isNull():
            self.setWindowIcon(application.windowIcon())
        self.setWindowTitle("Two-GeoJSON Road Matcher")
        self.resize(1000, 760)
        self.pipeline: RoadMatchingPipeline | None = None
        self._thread: QThread | None = None
        self._worker: Any = None
        self._settings = QSettings()

        self.file_1_edit = QLineEdit()
        self.file_2_edit = QLineEdit()
        self.output_edit = QLineEdit(
            str(Path.home() / "Documents" / "RoadMatcherOutputs")
        )
        self.road_id_edit = QLineEdit("OBJECTID")
        self.target_crs_edit = QLineEdit("EPSG:26916")
        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(0.1, 10000.0)
        self.buffer_spin.setDecimals(1)
        self.buffer_spin.setValue(50.0)
        self.buffer_spin.setSuffix(" m")
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 1000)
        self.batch_spin.setValue(30)
        self.basemap_checkbox = QCheckBox(
            "Show online street basemap during manual review"
        )
        self.basemap_checkbox.setChecked(False)

        self.analyze_button = QPushButton("Analyze GeoJSON Files")
        self.review_button = QPushButton("Open / Resume Manual Review")
        self.open_output_button = QPushButton("Open Output Folder")
        self.quit_button = QPushButton("Save Session && Quit Application")
        self.review_button.setEnabled(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label = QLabel("Select two GeoJSON files to begin.")
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)

        input_group = self._build_input_group()
        settings_group = self._build_settings_group()

        actions = QHBoxLayout()
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.review_button)
        actions.addWidget(self.open_output_button)
        actions.addWidget(self.quit_button)

        central = QWidget()
        layout = QVBoxLayout(central)

        branding = QHBoxLayout()
        logo_label = QLabel()
        application = QApplication.instance()
        if application is not None and not application.windowIcon().isNull():
            logo_label.setPixmap(application.windowIcon().pixmap(48, 48))
        logo_label.setFixedSize(52, 52)
        title_label = QLabel("<b style='font-size:20px'>Road Matcher</b><br>Road Junction Verification")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        branding.addWidget(logo_label)
        branding.addWidget(title_label)
        branding.addStretch(1)
        layout.addLayout(branding)
        layout.addWidget(input_group)
        layout.addWidget(settings_group)
        layout.addLayout(actions)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(QLabel("Processing log"))
        layout.addWidget(self.log_edit, 1)
        self.setCentralWidget(central)

        self.analyze_button.clicked.connect(self._start_analysis)
        self.review_button.clicked.connect(self._open_review)
        self.open_output_button.clicked.connect(self._open_output_folder)
        self.quit_button.clicked.connect(self.close)

        self._restore_settings()
        # Resume the last valid project automatically. Analysis must run again
        # to reconstruct notebook state before compatible saved decisions can
        # be merged into the in-memory review queue.
        # QTimer.singleShot(350, self._auto_start_last_project)

    def _build_input_group(self) -> QGroupBox:
        group = QGroupBox("Local files")
        layout = QGridLayout(group)
        browse_1 = QPushButton("Browse...")
        browse_2 = QPushButton("Browse...")
        browse_output = QPushButton("Browse...")
        browse_1.clicked.connect(lambda: self._select_file(self.file_1_edit))
        browse_2.clicked.connect(lambda: self._select_file(self.file_2_edit))
        browse_output.clicked.connect(self._select_output_dir)
        layout.addWidget(QLabel("GeoJSON file 1"), 0, 0)
        layout.addWidget(self.file_1_edit, 0, 1)
        layout.addWidget(browse_1, 0, 2)
        layout.addWidget(QLabel("GeoJSON file 2"), 1, 0)
        layout.addWidget(self.file_2_edit, 1, 1)
        layout.addWidget(browse_2, 1, 2)
        layout.addWidget(QLabel("Output folder"), 2, 0)
        layout.addWidget(self.output_edit, 2, 1)
        layout.addWidget(browse_output, 2, 2)
        return group

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("Notebook-compatible settings")
        form = QFormLayout(group)
        form.addRow("Stable road ID column", self.road_id_edit)
        form.addRow("Projected CRS", self.target_crs_edit)
        form.addRow("Candidate search distance", self.buffer_spin)
        form.addRow("Notebook rows per review batch", self.batch_spin)
        form.addRow("Map", self.basemap_checkbox)
        return group

    def _restore_settings(self) -> None:
        self.file_1_edit.setText(str(self._settings.value("paths/file_1", "")))
        self.file_2_edit.setText(str(self._settings.value("paths/file_2", "")))
        stored_output = self._settings.value("paths/output", "")
        if stored_output:
            self.output_edit.setText(str(stored_output))
        self.road_id_edit.setText(
            str(self._settings.value("pipeline/road_id", "OBJECTID"))
        )
        self.target_crs_edit.setText(
            str(self._settings.value("pipeline/target_crs", "EPSG:26916"))
        )
        try:
            self.buffer_spin.setValue(
                float(self._settings.value("pipeline/buffer_m", 50.0))
            )
            self.batch_spin.setValue(
                int(self._settings.value("pipeline/batch_size", 30))
            )
        except (TypeError, ValueError):
            LOGGER.exception("Stored GUI settings were invalid; defaults were retained.")
        basemap_value = str(
            self._settings.value("map/online_basemap", "false")
        ).lower()
        self.basemap_checkbox.setChecked(basemap_value in {"1", "true", "yes"})

    def _auto_start_last_project(self) -> None:
        """Automatically run the last valid project after application startup."""
        if self._thread is not None or self.pipeline is not None:
            return
        try:
            config = self._config().normalized()
            config.validate()
        except Exception:
            LOGGER.info(
                "Automatic startup skipped because the remembered input/output paths "
                "are not yet valid."
            )
            return

        state_dir = config.output_dir / ".road_matcher_state"
        has_saved_state = state_dir.is_dir() and any(
            state_dir.glob("*_manual_review_progress*.csv")
        )
        if has_saved_state:
            LOGGER.info(
                "Saved review state detected in %s; automatically rebuilding the "
                "pipeline and resuming the compatible session.",
                state_dir,
            )
            self.status_label.setText(
                "Saved review state detected; automatically resuming the last project..."
            )
        else:
            LOGGER.info(
                "No saved review state detected for the remembered valid project; "
                "automatically starting from the beginning."
            )
            self.status_label.setText(
                "No saved review state found; automatically starting the last project..."
            )
        self._start_analysis()

    def _save_settings(self) -> None:
        self._settings.setValue("paths/file_1", self.file_1_edit.text().strip())
        self._settings.setValue("paths/file_2", self.file_2_edit.text().strip())
        self._settings.setValue("paths/output", self.output_edit.text().strip())
        self._settings.setValue("pipeline/road_id", self.road_id_edit.text().strip())
        self._settings.setValue("pipeline/target_crs", self.target_crs_edit.text().strip())
        self._settings.setValue("pipeline/buffer_m", self.buffer_spin.value())
        self._settings.setValue("pipeline/batch_size", self.batch_spin.value())
        self._settings.setValue("map/online_basemap", self.basemap_checkbox.isChecked())
        self._settings.sync()

    def _select_file(self, target: QLineEdit) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select road GeoJSON",
            str(Path.home()),
            "GeoJSON files (*.geojson *.json);;All files (*)",
        )
        if filename:
            target.setText(filename)

    def _select_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            self.output_edit.text() or str(Path.home()),
        )
        if directory:
            self.output_edit.setText(directory)

    def _config(self) -> PipelineConfig:
        return PipelineConfig(
            county_file_1=Path(self.file_1_edit.text().strip()),
            county_file_2=Path(self.file_2_edit.text().strip()),
            output_dir=Path(self.output_edit.text().strip()),
            buffer_distance_meters=self.buffer_spin.value(),
            target_crs=self.target_crs_edit.text().strip(),
            road_id_column=self.road_id_edit.text().strip(),
            max_manual_batch_size=self.batch_spin.value(),
        )

    def _set_busy(self, busy: bool, status: str) -> None:
        self.analyze_button.setEnabled(not busy)
        self.review_button.setEnabled(not busy and self.pipeline is not None)
        self.quit_button.setEnabled(not busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1)
        self.status_label.setText(status)

    def _append_log(self, message: str) -> None:
        self.log_edit.append(message.replace("\n", "<br>"))

    def _start_analysis(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            LOGGER.warning("Analysis request ignored because a background task is active.")
            return
        try:
            config = self._config().normalized()
            config.validate()
        except Exception as exc:
            LOGGER.exception("Invalid pipeline configuration.")
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return

        self._save_settings()
        self.pipeline = None
        self.summary_label.clear()
        self.log_edit.clear()
        self._set_busy(True, "Analyzing road files...")
        LOGGER.info("Starting analysis for %s and %s", config.county_file_1, config.county_file_2)

        thread = QThread(self)
        worker = AnalysisWorker(config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.stage.connect(self._analysis_stage)
        worker.completed.connect(self._analysis_completed)
        worker.failed.connect(self._task_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._background_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _analysis_stage(self, position: int, total: int, filename: str) -> None:
        self.status_label.setText(f"Analysis stage {position} of {total}: {filename}")

    def _analysis_completed(self, pipeline: RoadMatchingPipeline) -> None:
        pipeline.set_logger(self._append_log)
        self.pipeline = pipeline
        summary = pipeline.review_summary()
        junctions = pipeline.junction_review_summary()
        session_text = (
            f"Loaded session: {pipeline.loaded_session_path}"
            if pipeline.loaded_session_path is not None
            else "No saved session was loaded"
        )
        self.summary_label.setText(
            f"Candidate pairs: {summary['total_candidates']} | "
            f"Safely rejected: {summary['safe_rejected']} "
            f"({summary['safe_reject_fraction']:.2%}) | "
            f"MANUAL_REVIEW pair evidence: {summary['selected']}<br>"
            f"Junction round {junctions['round']}: {junctions['selected']} shown | "
            f"Completed: {junctions['completed']} | Remaining: {junctions['remaining']} | "
            f"Deferred conflicts: {junctions['deferred']}<br>"
            f"Safe thresholds — global: {summary['global_threshold']:.6f}, "
            f"parallel: {summary['parallel_threshold']:.6f}, "
            f"near-90°: {summary['orthogonal_threshold']:.6f}<br>{session_text}"
        )
        self._set_busy(False, "Analysis complete.")
        self.review_button.setEnabled(junctions["selected"] > 0)
        QMessageBox.information(
            self,
            "Processing complete",
            f"Processing generated {summary['total_candidates']:,} pair candidates.\n\n"
            f"Safely rejected: {summary['safe_rejected']:,} "
            f"({summary['safe_reject_fraction']:.2%})\n"
            f"Pair evidence requiring human handling: {summary['selected']:,}\n\n"
            f"Junction round {junctions['round']} contains {junctions['selected']:,} "
            "non-overlapping junctions. Each road appears in at most one junction "
            "during this round."
        )
        if junctions["remaining"] > 0:
            self._open_review()
        elif junctions["selected"] > 0:
            self._start_junction_round_completion()
        else:
            self._start_finalization()

    def _open_review(self) -> None:
        if self.pipeline is None:
            return
        dialog = ManualReviewDialog(
            self.pipeline,
            include_basemap=self.basemap_checkbox.isChecked(),
            parent=self,
        )
        dialog.exec()

        if callable(getattr(self.pipeline, "junction_review_summary", None)):
            junctions = self.pipeline.junction_review_summary()
            pair_summary = self.pipeline.review_summary()
            self.summary_label.setText(
                f"Candidate pairs: {pair_summary['total_candidates']} | "
                f"Safely rejected: {pair_summary['safe_rejected']} "
                f"({pair_summary['safe_reject_fraction']:.2%})<br>"
                f"Junction round {junctions['round']}: {junctions['selected']} shown | "
                f"Completed: {junctions['completed']} | Remaining: {junctions['remaining']} | "
                f"Deferred: {junctions['deferred']}<br>"
                f"Junction state: {self.pipeline.junction_state_path()}"
            )
            if junctions["remaining"] == 0 and junctions["selected"] > 0:
                self._start_junction_round_completion()
            elif junctions["selected"] == 0:
                self._start_finalization()
            return

        # Backward-compatible path for older/fake pipelines used by tests.
        summary = self.pipeline.review_summary()
        self.summary_label.setText(
            f"Candidate pairs: {summary['total_candidates']} | "
            f"Safely rejected: {summary['safe_rejected']} "
            f"({summary['safe_reject_fraction']:.2%}) | "
            f"Manual review: {summary['selected']} "
            f"({summary['selected_fraction']:.2%}) | "
            f"Completed: {summary['completed']} | Remaining: {summary['remaining']}<br>"
            f"Safe thresholds — global: {summary['global_threshold']:.6f}, "
            f"parallel: {summary['parallel_threshold']:.6f}, "
            f"near-90°: {summary['orthogonal_threshold']:.6f}<br>"
            f"Session file: {self.pipeline.session_path}"
        )
        if summary["remaining"] == 0:
            self._start_finalization()

    def _start_junction_round_completion(self) -> None:
        if self.pipeline is None:
            return
        junctions = self.pipeline.junction_review_summary()
        if junctions["remaining"] > 0:
            QMessageBox.warning(
                self,
                "Junction review incomplete",
                f"{junctions['remaining']} junction(s) remain unanswered in this round.",
            )
            return
        if junctions["selected"] == 0:
            self._start_finalization()
            return
        self._set_busy(True, f"Applying junction round {junctions['round']}...")
        thread = QThread(self)
        worker = JunctionRoundWorker(self.pipeline)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.stage.connect(self._analysis_stage)
        worker.completed.connect(self._junction_round_completed)
        worker.failed.connect(self._task_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._background_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _junction_round_completed(self, result: Any) -> None:
        if self.pipeline is None:
            return
        self.pipeline.set_logger(self._append_log)
        junctions = self.pipeline.junction_review_summary()
        self._set_busy(False, f"Junction round {result.completed_round} completed.")
        self.summary_label.setText(
            f"Round {result.completed_round}: accepted {result.accepted_junctions}, "
            f"rejected {result.rejected_junctions}. "
            f"Geometry changed: {'yes' if result.geometry_changed else 'no'}.<br>"
            f"Round {junctions['round']}: {junctions['selected']} junction(s) ready; "
            f"deferred conflicts: {junctions['deferred']}."
        )
        if junctions["selected"] > 0:
            QTimer.singleShot(0, self._open_review)
        else:
            QTimer.singleShot(0, self._start_finalization)

    def _start_finalization(self) -> None:
        if self.pipeline is None:
            return
        if callable(getattr(self.pipeline, "junction_review_summary", None)):
            junctions = self.pipeline.junction_review_summary()
            if junctions["remaining"] > 0 or junctions["selected"] > 0:
                QMessageBox.warning(
                    self,
                    "Junction review incomplete",
                    "The current junction round must be fully reviewed and committed "
                    "before final outputs are created.",
                )
                return
        else:
            summary = self.pipeline.review_summary()
            if summary["remaining"] > 0:
                QMessageBox.warning(
                    self,
                    "Manual review incomplete",
                    f"{summary['remaining']} pairs remain unanswered.",
                )
                return
        self._set_busy(True, "Creating final GeoJSON files and audit CSVs...")
        LOGGER.info("Starting final output creation.")
        thread = QThread(self)
        worker = FinalizationWorker(self.pipeline)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.completed.connect(self._finalization_completed)
        worker.failed.connect(self._task_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._background_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _finalization_completed(self, result: FinalizationResult) -> None:
        LOGGER.info("Final outputs created successfully: %s", result)
        self._set_busy(False, "Final outputs created successfully.")
        self.summary_label.setText(
            f"Accepted pairs: {result.accepted_pairs} | Rejected pairs: {result.rejected_pairs}<br>"
            f"Updated GeoJSON 1: {result.county_1_output}<br>"
            f"Updated GeoJSON 2: {result.county_2_output}<br>"
            f"Final decisions: {result.final_decisions_csv}"
        )
        QMessageBox.information(
            self,
            "Road matching complete",
            "The two updated GeoJSON files and audit CSV files were created in "
            "the selected output folder.",
        )

    def _task_failed(self, traceback_text: str) -> None:
        LOGGER.error("Background operation failed:\n%s", traceback_text)
        self._append_log(traceback_text)
        self._set_busy(False, "Operation failed. Review the processing log.")
        QMessageBox.critical(
            self,
            "Operation failed",
            "The operation failed. The complete traceback is available in the "
            "processing log and console.",
        )

    def _background_thread_finished(self) -> None:
        LOGGER.info("Background Qt thread finished.")
        self._worker = None
        self._thread = None

    def _save_session_before_exit(self) -> bool:
        if self.pipeline is None:
            return True
        try:
            path = self.pipeline.save_session(force=True, reason="application quit")
            if path is not None:
                LOGGER.info("Session saved before application exit: %s", path)
            return True
        except Exception as exc:
            LOGGER.exception("Could not save the session before application exit.")
            QMessageBox.critical(
                self,
                "Could not save progress",
                "The application will remain open because the current session could "
                f"not be saved. Close the CSV in Excel and try Quit again.\n\n{exc}",
            )
            return False

    def closeEvent(self, event: QCloseEvent) -> None:
        thread = self._thread
        if thread is not None and thread.isRunning():
            QMessageBox.information(
                self,
                "Operation still running",
                "Please wait for the current analysis or finalization operation to "
                "finish before closing the application.",
            )
            event.ignore()
            return
        if not self._save_session_before_exit():
            event.ignore()
            return
        self._save_settings()
        LOGGER.info("Main window accepted the application close event.")
        event.accept()

    def _open_output_folder(self) -> None:
        directory = Path(self.output_edit.text().strip()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))
