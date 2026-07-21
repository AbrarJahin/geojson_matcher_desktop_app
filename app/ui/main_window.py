from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
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
from app.workers.tasks import AnalysisWorker, FinalizationWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Two-GeoJSON Road Matcher")
        self.resize(1000, 760)
        self.pipeline: RoadMatchingPipeline | None = None
        self._thread: QThread | None = None
        self._worker: Any = None

        self.file_1_edit = QLineEdit()
        self.file_2_edit = QLineEdit()
        self.output_edit = QLineEdit(str(Path.home() / "Documents" / "RoadMatcherOutputs"))
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
        self.basemap_checkbox = QCheckBox("Show online street basemap during manual review (loads asynchronously)")
        self.basemap_checkbox.setChecked(True)

        self.analyze_button = QPushButton("Analyze GeoJSON Files")
        self.review_button = QPushButton("Open / Resume Manual Review")
        self.finalize_button = QPushButton("Create Final Outputs")
        self.open_output_button = QPushButton("Open Output Folder")
        self.review_button.setEnabled(False)
        self.finalize_button.setEnabled(False)

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
        actions.addWidget(self.finalize_button)
        actions.addWidget(self.open_output_button)

        central = QWidget()
        layout = QVBoxLayout(central)
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
        self.finalize_button.clicked.connect(self._start_finalization)
        self.open_output_button.clicked.connect(self._open_output_folder)

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
        form.addRow("Maximum manual batch size", self.batch_spin)
        form.addRow("Map", self.basemap_checkbox)
        return group

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
            self, "Select output folder", self.output_edit.text() or str(Path.home())
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
        self.finalize_button.setEnabled(not busy and self.pipeline is not None)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1)
        self.status_label.setText(status)

    def _append_log(self, message: str) -> None:
        self.log_edit.append(message.replace("\n", "<br>"))

    def _start_analysis(self) -> None:
        try:
            config = self._config().normalized()
            config.validate()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return

        self.pipeline = None
        self.summary_label.clear()
        self.log_edit.clear()
        self._set_busy(True, "Analyzing road files...")

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
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _analysis_stage(self, position: int, total: int, filename: str) -> None:
        self.status_label.setText(f"Analysis stage {position} of {total}: {filename}")

    def _analysis_completed(self, pipeline: RoadMatchingPipeline) -> None:
        self.pipeline = pipeline
        summary = pipeline.review_summary()
        self.summary_label.setText(
            f"Candidate pairs: {summary['total_candidates']} | "
            f"Manual review selected: {summary['selected']} | "
            f"Completed: {summary['completed']} | Remaining: {summary['remaining']} | "
            f"Optimized threshold: {summary['threshold']:.6f}"
        )
        self._set_busy(False, "Analysis complete.")
        self.review_button.setEnabled(summary["selected"] > 0)
        self.finalize_button.setEnabled(summary["remaining"] == 0)
        if summary["remaining"] > 0:
            self._open_review()
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
        summary = self.pipeline.review_summary()
        self.summary_label.setText(
            f"Candidate pairs: {summary['total_candidates']} | Manual review selected: {summary['selected']} | "
            f"Completed: {summary['completed']} | Remaining: {summary['remaining']} | "
            f"Optimized threshold: {summary['threshold']:.6f}"
        )
        self.finalize_button.setEnabled(summary["remaining"] == 0)
        if summary["remaining"] == 0:
            self._start_finalization()

    def _start_finalization(self) -> None:
        if self.pipeline is None:
            return
        summary = self.pipeline.review_summary()
        if summary["remaining"] > 0:
            QMessageBox.warning(
                self, "Manual review incomplete", f"{summary['remaining']} pairs remain unanswered."
            )
            return
        self._set_busy(True, "Creating final GeoJSON files and audit CSVs...")
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
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _finalization_completed(self, result: FinalizationResult) -> None:
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
            "The two updated GeoJSON files and audit CSV files were created in the selected output folder.",
        )

    def _task_failed(self, traceback_text: str) -> None:
        self._append_log(traceback_text)
        self._set_busy(False, "Operation failed. Review the processing log.")
        QMessageBox.critical(
            self,
            "Operation failed",
            "The operation failed. The complete traceback is available in the processing log.",
        )

    def _open_output_folder(self) -> None:
        directory = Path(self.output_edit.text().strip()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))
