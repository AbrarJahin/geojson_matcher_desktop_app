from __future__ import annotations

import html
import logging
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.ui.map_canvas import InteractiveMapCanvas, MapNavigationToolbar

LOGGER = logging.getLogger(__name__)


def _format_probability(value: Any) -> str:
    try:
        if pd.isna(value):
            return "NA"
        return f"{float(value):.6f}"
    except Exception:
        return str(value)


class ManualReviewDialog(QDialog):
    """Full-work-area manual-review window.

    Every pair not classified as SAFE_REJECT is shown in the existing map UI.
    ``pipeline.review_rows`` presents the complete MANUAL_REVIEW set from lowest
    combined probability to highest. Yes/No decisions remain in RAM until the
    application quit flow.
    """

    all_completed = Signal()

    def __init__(self, pipeline: Any, include_basemap: bool = True, parent: Any = None):
        super().__init__(parent)
        self.pipeline = pipeline
        self._changing_pair = False
        self._closing = False
        self._screen_fitted = False
        self.rows = self._sort_rows_by_combined_probability(
            pipeline.review_rows(include_completed=True)
        )
        self.current_index = self._first_pending_index()
        self.setWindowTitle("Manual Road-Pair Verification")
        self.setSizeGripEnabled(False)
        self.resize(1280, 820)

        self.canvas = InteractiveMapCanvas(self)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.toolbar = MapNavigationToolbar(self.canvas, self)
        self.canvas.toolbar = self.toolbar

        self.position_label = QLabel()
        self.position_label.setWordWrap(True)
        self.position_label.setStyleSheet("font-size: 15px; font-weight: 700;")

        self.ids_label = QLabel()
        self.ids_label.setWordWrap(True)
        self.ids_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.probability_label = QLabel()
        self.probability_label.setWordWrap(True)
        self.probability_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)

        self.saved_label = QLabel()
        self.saved_label.setWordWrap(True)
        self.saved_label.setStyleSheet("font-weight: 600;")

        self.policy_label = QLabel()
        self.policy_label.setWordWrap(True)
        self.policy_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.legend_label = QLabel()
        self.legend_label.setWordWrap(True)
        self.legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.legend_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(max(len(self.rows), 1))
        self.progress.setFormat("%v of %m reviewed (%p%)")

        self.basemap_checkbox = QCheckBox("Online street basemap")
        self.basemap_checkbox.setChecked(include_basemap)
        self.basemap_checkbox.toggled.connect(self._redraw)

        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.no_button = QPushButton("No — Do not connect")
        self.yes_button = QPushButton("Yes — Connect")
        self.close_button = QPushButton("Return to Main Window")
        self.quit_button = QPushButton("Save Session && Quit Application")

        self.no_button.setMinimumHeight(44)
        self.yes_button.setMinimumHeight(44)
        self.no_button.setStyleSheet("font-weight: 700;")
        self.yes_button.setStyleSheet("font-weight: 700;")

        self.previous_button.clicked.connect(self._previous)
        self.next_button.clicked.connect(self._next)
        self.no_button.clicked.connect(lambda: self._record("no"))
        self.yes_button.clicked.connect(lambda: self._record("yes"))
        self.close_button.clicked.connect(self.accept)
        self.quit_button.clicked.connect(self._quit_application)

        self._shortcuts = [
            QShortcut(QKeySequence("Y"), self, activated=lambda: self._record("yes")),
            QShortcut(QKeySequence("N"), self, activated=lambda: self._record("no")),
            QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._previous),
            QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._next),
        ]

        sidebar = self._build_sidebar()

        map_widget = QWidget()
        map_layout = QVBoxLayout(map_widget)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(4)
        map_layout.addWidget(self.toolbar)
        map_layout.addWidget(self.canvas, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(sidebar)
        splitter.addWidget(map_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([370, 1200])

        decisions = QHBoxLayout()
        decisions.setSpacing(10)
        decisions.addWidget(self.no_button, 2)
        decisions.addWidget(self.yes_button, 2)
        decisions.addWidget(self.close_button, 1)
        decisions.addWidget(self.quit_button, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(splitter, 1)
        layout.addLayout(decisions)

        # Maximize through the window manager so Windows uses availableGeometry,
        # which excludes the taskbar/start-menu work area.
        QTimer.singleShot(0, self._fit_to_available_screen)

        if self.rows.empty:
            LOGGER.info("No pairs require manual review; continuing to final outputs.")
            self.all_completed.emit()
            QTimer.singleShot(0, self.accept)
            return
        self._show_current()


    @staticmethod
    def _sort_rows_by_combined_probability(rows: pd.DataFrame) -> pd.DataFrame:
        """Guarantee monotonically increasing desktop review probability."""
        ordered = rows.copy()
        ordered["_desktop_probability_order"] = pd.to_numeric(
            ordered.get("probablity"), errors="coerce"
        )
        if "manual_review_rank" not in ordered.columns:
            ordered["manual_review_rank"] = range(1, len(ordered) + 1)
        if "pair_key" not in ordered.columns:
            ordered["pair_key"] = ordered.index.astype(str)
        return (
            ordered.sort_values(
                ["_desktop_probability_order", "manual_review_rank", "pair_key"],
                ascending=[True, True, True],
                na_position="last",
                kind="mergesort",
            )
            .drop(columns=["_desktop_probability_order"])
            .reset_index(drop=True)
        )

    def _build_sidebar(self) -> QScrollArea:
        details_group = QGroupBox("Review details")
        details_layout = QVBoxLayout(details_group)
        details_layout.addWidget(self.position_label)
        details_layout.addWidget(self.ids_label)
        details_layout.addWidget(self.probability_label)
        details_layout.addWidget(self.reason_label)
        details_layout.addWidget(self.saved_label)
        details_layout.addWidget(self.progress)

        policy_group = QGroupBox("Review policy")
        policy_layout = QVBoxLayout(policy_group)
        policy_layout.addWidget(self.policy_label)

        legend_group = QGroupBox("Map legend")
        legend_layout = QVBoxLayout(legend_group)
        legend_layout.addWidget(self.legend_label)

        navigation_group = QGroupBox("Navigation")
        navigation_layout = QVBoxLayout(navigation_group)
        nav_buttons = QHBoxLayout()
        nav_buttons.addWidget(self.previous_button)
        nav_buttons.addWidget(self.next_button)
        navigation_layout.addLayout(nav_buttons)
        navigation_layout.addWidget(self.basemap_checkbox)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(8)
        content_layout.addWidget(details_group)
        content_layout.addWidget(policy_group)
        content_layout.addWidget(legend_group)
        content_layout.addWidget(navigation_group)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(440)
        return scroll

    def _fit_to_available_screen(self) -> None:
        if self._closing:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.setMaximumSize(available.size())
        # Let the native window manager account for frame/title-bar dimensions.
        self.showMaximized()
        self._screen_fitted = True
        LOGGER.info(
            "Manual-review window maximized to usable screen area: %sx%s at (%s,%s).",
            available.width(),
            available.height(),
            available.x(),
            available.y(),
        )

    def _first_pending_index(self) -> int:
        if self.rows.empty:
            return 0
        pending = self.rows.index[self.rows["manual_decision"].isna()].tolist()
        return int(pending[0]) if pending else 0

    def _row(self) -> pd.Series:
        return self.rows.iloc[self.current_index]

    def _review_policy_text(self) -> str:
        try:
            diagnostics = self.pipeline.manual_review_diagnostics()
            return (
                f"Safely rejected automatically: <b>{diagnostics['safe_rejected']}</b> "
                f"of <b>{diagnostics['total_candidates']}</b> candidates "
                f"({diagnostics['safe_reject_fraction']:.2%}).<br>"
                f"Complete manual-review queue: <b>{diagnostics['selected']}</b> "
                f"pairs ({diagnostics['selected_fraction']:.2%}).<br><br>"
                "Every candidate not safely rejected is included. Display order: "
                "<b>lowest combined probability → highest</b>."
            )
        except Exception:
            LOGGER.exception("Could not build manual-review policy details.")
            return (
                "Every candidate not safely rejected is included. Display order: "
                "<b>lowest combined probability → highest</b>."
            )

    def _legend_html(self, road_name_1: str, road_name_2: str) -> str:
        county_1 = html.escape(str(self.pipeline.county_1_name))
        county_2 = html.escape(str(self.pipeline.county_2_name))
        name_1 = html.escape(str(road_name_1))
        name_2 = html.escape(str(road_name_2))
        return (
            f"<span style='color:#2f5cff;font-size:18px;'>━━━━</span> "
            f"<b>{county_1}:</b> {name_1}<br>"
            f"<span style='color:#f0a128;font-size:18px;'>━━━━</span> "
            f"<b>{county_2}:</b> {name_2}<br>"
            "<span style='color:#111111;font-size:18px;'>┄┄┄┄</span> Current gap<br>"
            f"<span style='color:#e74c3c;font-size:18px;'>●</span> {county_1} contact<br>"
            f"<span style='color:#39a852;font-size:18px;'>●</span> {county_2} contact<br>"
            "<span style='color:#32cd32;font-size:18px;'>✖</span> Proposed shared midpoint"
        )

    def _show_current(self) -> None:
        if self.rows.empty or self._closing:
            return
        row = self._row()
        completed = int(self.rows["manual_decision"].notna().sum())
        self.position_label.setText(
            f"Pair {self.current_index + 1} of {len(self.rows)}<br>"
            f"Completed {completed}; remaining {len(self.rows) - completed}"
        )
        self.ids_label.setText(
            f"<b>{html.escape(str(self.pipeline.county_1_name))} road ID:</b> "
            f"{html.escape(str(row['county_1_id']))}<br>"
            f"<b>{html.escape(str(self.pipeline.county_2_name))} road ID:</b> "
            f"{html.escape(str(row['county_2_id']))}"
        )
        self.probability_label.setText(
            "<b>Geometric:</b> "
            + _format_probability(row.get("geometric_valid_pair_probability"))
            + "<br><b>Textual:</b> "
            + _format_probability(row.get("textual_valid_pair_probability"))
            + "<br><b>Combined:</b> "
            + _format_probability(row.get("probablity"))
            + "<br><b>Applicable Safe Reject threshold:</b> "
            + _format_probability(
                row.get("safe17_applied_threshold", self.pipeline.optimized_threshold)
            )
        )
        self.reason_label.setText(
            "<b>Selection reason:</b><br>"
            + html.escape(str(row.get("manual_review_selected_from", "manual review")))
        )
        current_decision = row.get("manual_decision")
        self.saved_label.setText(
            "Decision: not answered"
            if pd.isna(current_decision)
            else f"Decision in memory: {str(current_decision).upper()}"
        )
        self.policy_label.setText(self._review_policy_text())
        self.progress.setValue(completed)
        self.previous_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.rows) - 1)
        road_name_1, road_name_2 = self.canvas.draw_pair(
            self.pipeline, row, include_basemap=self.basemap_checkbox.isChecked()
        )
        self.legend_label.setText(self._legend_html(road_name_1, road_name_2))

    def _redraw(self) -> None:
        if not self.rows.empty and not self._changing_pair and not self._closing:
            self._show_current()

    def _previous(self) -> None:
        if self._changing_pair or self._closing:
            return
        if self.current_index > 0:
            self.current_index -= 1
            self._show_current()

    def _next(self) -> None:
        if self._changing_pair or self._closing:
            return
        if self.current_index < len(self.rows) - 1:
            self.current_index += 1
            self._show_current()

    def _set_transition_controls_enabled(self, enabled: bool) -> None:
        self.no_button.setEnabled(enabled)
        self.yes_button.setEnabled(enabled)
        self.previous_button.setEnabled(enabled and self.current_index > 0)
        self.next_button.setEnabled(
            enabled and self.current_index < len(self.rows) - 1
        )
        self.basemap_checkbox.setEnabled(enabled)
        self.close_button.setEnabled(enabled)
        self.quit_button.setEnabled(enabled)

    def _finish_pair_transition(self) -> None:
        try:
            self._show_current()
        except Exception:
            LOGGER.exception("Failed while displaying the next manual-review pair.")
            QMessageBox.critical(
                self,
                "Could not display pair",
                "The decision is still in memory, but the next map could not be "
                "displayed. See the console log for the complete traceback.",
            )
        finally:
            self._changing_pair = False
            if not self._closing:
                self._set_transition_controls_enabled(True)

    def _record(self, decision: str) -> None:
        if self.rows.empty or self._changing_pair or self._closing:
            return

        self._changing_pair = True
        self._set_transition_controls_enabled(False)
        self.saved_label.setText(f"Recording decision in memory: {decision.upper()}...")

        row = self._row()
        try:
            self.pipeline.record_manual_decision(str(row["pair_key"]), decision)
            self.rows.at[self.rows.index[self.current_index], "manual_decision"] = decision
        except Exception as exc:
            LOGGER.exception("Could not record the manual decision in memory.")
            self._changing_pair = False
            self._set_transition_controls_enabled(True)
            QMessageBox.critical(self, "Could not record decision", str(exc))
            return

        pending_positions = self.rows.index[
            self.rows["manual_decision"].isna()
        ].tolist()
        if not pending_positions:
            LOGGER.info(
                "Manual review completed in RAM. Closing review and starting "
                "automatic final-output creation."
            )
            self.saved_label.setText(
                "All selected pairs reviewed. Creating final outputs automatically..."
            )
            self._changing_pair = False
            self.all_completed.emit()
            self.accept()
            return

        later = [position for position in pending_positions if position > self.current_index]
        self.current_index = int(later[0] if later else pending_positions[0])

        # Return to Qt before the next map redraw. This keeps button feedback
        # immediate and prevents duplicate clicks from entering the transition.
        QTimer.singleShot(0, self._finish_pair_transition)

    def _quit_application(self) -> None:
        """Close review, then delegate the only durable session save to MainWindow."""
        parent = self.parentWidget()
        self._finish_without_saving(QDialog.DialogCode.Accepted)
        if parent is not None:
            QTimer.singleShot(0, parent.close)

    def _finish_without_saving(self, result: int) -> None:
        """Close only the review window; application state remains in RAM."""
        if self._closing:
            return
        self._closing = True
        self._set_transition_controls_enabled(False)
        try:
            self.canvas.shutdown()
        except Exception:
            LOGGER.exception("Map shutdown reported an error.")
        QDialog.done(self, result)

    def accept(self) -> None:
        self._finish_without_saving(QDialog.DialogCode.Accepted)

    def reject(self) -> None:
        # Window X and Escape close only this review window. The main window
        # remains the sole owner of durable session saving.
        self._finish_without_saving(QDialog.DialogCode.Rejected)

    def done(self, result: int) -> None:
        self._finish_without_saving(result)
