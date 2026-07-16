from __future__ import annotations

from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
)

from app.ui.map_canvas import InteractiveMapCanvas, MapNavigationToolbar


def _format_probability(value: Any) -> str:
    try:
        if pd.isna(value):
            return "NA"
        return f"{float(value):.6f}"
    except Exception:
        return str(value)


class ManualReviewDialog(QDialog):
    all_completed = Signal()

    def __init__(self, pipeline: Any, include_basemap: bool = True, parent: Any = None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.rows = pipeline.review_rows(include_completed=True)
        self.current_index = self._first_pending_index()
        self.setWindowTitle("Manual Road-Pair Verification")
        self.resize(1250, 900)

        self.canvas = InteractiveMapCanvas(self)
        self.toolbar = MapNavigationToolbar(self.canvas, self)
        self.canvas.toolbar = self.toolbar

        self.position_label = QLabel()
        self.ids_label = QLabel()
        self.ids_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.probability_label = QLabel()
        self.probability_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        self.saved_label = QLabel()
        self.saved_label.setStyleSheet("font-weight: 600;")

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(max(len(self.rows), 1))

        self.basemap_checkbox = QCheckBox("Online street basemap")
        self.basemap_checkbox.setChecked(include_basemap)
        self.basemap_checkbox.toggled.connect(self._redraw)

        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.no_button = QPushButton("No — Do not connect")
        self.yes_button = QPushButton("Yes — Connect")
        self.close_button = QPushButton("Save && Close")

        self.no_button.setMinimumHeight(42)
        self.yes_button.setMinimumHeight(42)
        self.no_button.setStyleSheet("font-weight: 700;")
        self.yes_button.setStyleSheet("font-weight: 700;")

        self.previous_button.clicked.connect(self._previous)
        self.next_button.clicked.connect(self._next)
        self.no_button.clicked.connect(lambda: self._record("no"))
        self.yes_button.clicked.connect(lambda: self._record("yes"))
        self.close_button.clicked.connect(self.accept)

        QShortcut(QKeySequence("Y"), self, activated=lambda: self._record("yes"))
        QShortcut(QKeySequence("N"), self, activated=lambda: self._record("no"))
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=self._previous)
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self._next)

        details = QGridLayout()
        details.addWidget(self.position_label, 0, 0, 1, 2)
        details.addWidget(self.ids_label, 1, 0, 1, 2)
        details.addWidget(self.probability_label, 2, 0, 1, 2)
        details.addWidget(self.reason_label, 3, 0, 1, 2)
        details.addWidget(self.saved_label, 4, 0, 1, 2)
        details.addWidget(self.progress, 5, 0, 1, 2)

        navigation = QHBoxLayout()
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        navigation.addStretch(1)
        navigation.addWidget(self.basemap_checkbox)

        decisions = QHBoxLayout()
        decisions.addWidget(self.no_button)
        decisions.addWidget(self.yes_button)
        decisions.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addLayout(details)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(navigation)
        layout.addLayout(decisions)

        if self.rows.empty:
            QMessageBox.information(self, "Manual review", "No pairs require manual review.")
            self.all_completed.emit()
            self.accept()
            return
        self._show_current()

    def _first_pending_index(self) -> int:
        if self.rows.empty:
            return 0
        pending = self.rows.index[self.rows["manual_decision"].isna()].tolist()
        return int(pending[0]) if pending else 0

    def _row(self) -> pd.Series:
        return self.rows.iloc[self.current_index]

    def _show_current(self) -> None:
        row = self._row()
        completed = int(self.rows["manual_decision"].notna().sum())
        self.position_label.setText(
            f"Pair {self.current_index + 1} of {len(self.rows)} — completed {completed}, remaining {len(self.rows) - completed}"
        )
        self.ids_label.setText(
            f"{self.pipeline.county_1_name} road ID: {row['county_1_id']}    |    "
            f"{self.pipeline.county_2_name} road ID: {row['county_2_id']}"
        )
        self.probability_label.setText(
            "Geometric: " + _format_probability(row.get("geometric_valid_pair_probability"))
            + "    Textual: " + _format_probability(row.get("textual_valid_pair_probability"))
            + "    Combined: " + _format_probability(row.get("probablity"))
            + "    Threshold: " + _format_probability(self.pipeline.optimized_threshold)
        )
        self.reason_label.setText(
            f"Selection reason: {row.get('manual_review_selected_from', 'manual review')}"
        )
        current_decision = row.get("manual_decision")
        self.saved_label.setText(
            "Saved decision: not answered" if pd.isna(current_decision)
            else f"Saved decision: {str(current_decision).upper()}"
        )
        self.progress.setValue(completed)
        self.previous_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.rows) - 1)
        self.canvas.draw_pair(
            self.pipeline, row, include_basemap=self.basemap_checkbox.isChecked()
        )

    def _redraw(self) -> None:
        if not self.rows.empty:
            self._show_current()

    def _previous(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self._show_current()

    def _next(self) -> None:
        if self.current_index < len(self.rows) - 1:
            self.current_index += 1
            self._show_current()

    def _record(self, decision: str) -> None:
        if self.rows.empty:
            return
        row = self._row()
        try:
            self.pipeline.record_manual_decision(str(row["pair_key"]), decision)
            self.rows.at[self.rows.index[self.current_index], "manual_decision"] = decision
        except Exception as exc:
            QMessageBox.critical(self, "Could not save decision", str(exc))
            return

        pending_positions = self.rows.index[self.rows["manual_decision"].isna()].tolist()
        if not pending_positions:
            self._show_current()
            QMessageBox.information(
                self,
                "Manual review complete",
                "All selected road pairs have been reviewed. Final outputs will now be created.",
            )
            self.all_completed.emit()
            self.accept()
            return

        later = [position for position in pending_positions if position > self.current_index]
        self.current_index = int(later[0] if later else pending_positions[0])
        self._show_current()
