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

from app.core.junctions import format_address_summary
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
        application = QApplication.instance()
        if application is not None and not application.windowIcon().isNull():
            self.setWindowIcon(application.windowIcon())
        self.pipeline = pipeline
        self._junction_mode = callable(getattr(pipeline, "junction_review_items", None))
        self._changing_pair = False
        self._closing = False
        self._screen_fitted = False
        self._member_checkboxes: dict[str, QCheckBox] = {}
        if self._junction_mode:
            self.junctions = list(pipeline.junction_review_items(include_completed=True))
            self.rows = pd.DataFrame()
        else:
            self.junctions = []
            self.rows = self._sort_rows_by_combined_probability(
                pipeline.review_rows(include_completed=True)
            )
        self.current_index = self._first_pending_index()
        self.setWindowTitle(
            "Manual Junction Verification" if self._junction_mode else "Manual Road-Pair Verification"
        )
        self.setSizeGripEnabled(True)
        self.resize(1280, 820)

        self.canvas = InteractiveMapCanvas(self)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.toolbar = MapNavigationToolbar(self.canvas, self)
        self.canvas.toolbar = self.toolbar
        self.toolbar.set_junction_mode_available(self._junction_mode)
        self.canvas.junction_point_changed.connect(self._junction_point_moved)

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
        self.progress.setMaximum(max(self._item_count(), 1))
        self.progress.setFormat("%v of %m reviewed (%p%)")

        self.basemap_checkbox = QCheckBox("Online street basemap")
        self.basemap_checkbox.setChecked(include_basemap)
        self.basemap_checkbox.toggled.connect(self._redraw)

        self.axis_labels_checkbox = QCheckBox("Axis labels")
        self.axis_labels_checkbox.setChecked(True)
        self.axis_labels_checkbox.setToolTip(
            "Show or hide the X/Y axis titles, tick marks, and coordinate labels."
        )
        self.axis_labels_checkbox.toggled.connect(
            self._apply_axis_label_visibility
        )

        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.no_button = QPushButton(
            "Reject Junction" if self._junction_mode else "No — Do not connect"
        )
        self.yes_button = QPushButton(
            "Accept Junction" if self._junction_mode else "Yes — Connect"
        )
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

        # Start large inside the usable work area, but do not maximize or impose
        # a permanent maximum size. The native frame therefore remains freely
        # resizable and can use a larger monitor after a screen move.
        QTimer.singleShot(0, self._fit_to_available_screen)

        if self._item_count() == 0:
            LOGGER.info("No review items remain; continuing automatically.")
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

        self.members_group = QGroupBox("Junction roads")
        self.members_layout = QVBoxLayout(self.members_group)
        self.reset_point_button = QPushButton("Reset Suggested Point")
        self.reset_point_button.clicked.connect(self._reset_junction_point)
        self.members_layout.addWidget(self.reset_point_button)
        self.members_group.setVisible(self._junction_mode)

        self.address_group = QGroupBox("Selected road address ranges")
        address_layout = QVBoxLayout(self.address_group)
        self.address_label = QLabel("Addr: —")
        self.address_label.setWordWrap(True)
        self.address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        address_layout.addWidget(self.address_label)

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
        map_options = QHBoxLayout()
        map_options.addWidget(self.basemap_checkbox)
        map_options.addWidget(self.axis_labels_checkbox)
        map_options.addStretch(1)
        navigation_layout.addLayout(map_options)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(8)
        content_layout.addWidget(details_group)
        content_layout.addWidget(self.members_group)
        content_layout.addWidget(self.address_group)
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
        # Use most of the current work area without entering the maximized state.
        # Keeping the default unconstrained maximum size is important: users can
        # resize normally and can move the dialog to a larger monitor later.
        target_width = max(1, int(available.width() * 0.92))
        target_height = max(1, int(available.height() * 0.92))
        self.resize(target_width, target_height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())
        self._screen_fitted = True
        LOGGER.info(
            "Manual-review window fitted to usable screen area: %sx%s within %sx%s at (%s,%s).",
            target_width,
            target_height,
            available.width(),
            available.height(),
            available.x(),
            available.y(),
        )

    def _item_count(self) -> int:
        return len(self.junctions) if self._junction_mode else len(self.rows)

    def _first_pending_index(self) -> int:
        if self._junction_mode:
            for index, proposal in enumerate(self.junctions):
                if proposal.decision is None:
                    return index
            return 0
        if self.rows.empty:
            return 0
        pending = self.rows.index[self.rows["manual_decision"].isna()].tolist()
        return int(pending[0]) if pending else 0

    def _row(self) -> Any:
        if self._junction_mode:
            return self.junctions[self.current_index]
        return self.rows.iloc[self.current_index]

    def _review_policy_text(self) -> str:
        if self._junction_mode:
            try:
                diagnostics = self.pipeline.manual_review_diagnostics()
                junctions = self.pipeline.junction_review_summary()
                return (
                    f"Safely rejected automatically: <b>{diagnostics['safe_rejected']}</b> "
                    f"of <b>{diagnostics['total_candidates']}</b> pair candidates "
                    f"({diagnostics['safe_reject_fraction']:.2%}).<br>"
                    f"Round <b>{junctions['round']}</b>: <b>{junctions['selected']}</b> "
                    f"non-overlapping junctions; <b>{junctions['deferred']}</b> competing "
                    "junction proposal(s) deferred to a later round.<br><br>"
                    "Every review item is a junction (including two-road junctions). "
                    "A road appears in at most one junction in the current round."
                )
            except Exception:
                LOGGER.exception("Could not build junction-review policy details.")
                return "Every review item is a junction built from MANUAL_REVIEW pair evidence."
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

    def _clear_member_controls(self) -> None:
        if not hasattr(self, "members_layout"):
            return
        while self.members_layout.count() > 1:
            item = self.members_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._member_checkboxes = {}

    def _junction_member_toggled(self, member_key: str, checked: bool) -> None:
        if not self._junction_mode or self._closing or self._changing_pair:
            return
        proposal = self._row()
        for member in proposal.members:
            if member.key == member_key:
                member.selected = bool(checked)
                break
        self._show_current_junction(redraw_controls=False)

    def _junction_point_moved(self, x: float, y: float) -> None:
        if not self._junction_mode or self._closing or self._item_count() == 0:
            return
        proposal = self._row()
        proposal.junction_x = float(x)
        proposal.junction_y = float(y)
        self._update_junction_status_labels(proposal)

    def _reset_junction_point(self) -> None:
        if not self._junction_mode or self._item_count() == 0:
            return
        proposal = self._row()
        proposal.junction_x = float(proposal.suggested_x)
        proposal.junction_y = float(proposal.suggested_y)
        self._show_current_junction(redraw_controls=False)

    def _update_junction_status_labels(self, proposal: Any) -> None:
        selected = proposal.selected_members
        movement_lines = []
        from shapely.geometry import Point
        junction = Point(float(proposal.junction_x), float(proposal.junction_y))
        for member in proposal.members:
            movement = float(member.contact_point.distance(junction))
            marker = "✓" if member.selected else "–"
            movement_lines.append(
                f"{marker} {html.escape(member.road_name)} ({member.attachment_type}): "
                f"{movement:.2f} m"
            )
        self.ids_label.setText(
            f"<b>Selected roads:</b> {len(selected)} of {len(proposal.members)}<br>"
            + "<br>".join(movement_lines)
        )
        self.saved_label.setText(
            "Decision: not answered"
            if proposal.decision is None
            else f"Decision in memory: {proposal.decision.upper()}"
        )

    def _update_address_ranges(self, proposal: Any) -> None:
        lines = []
        for member in proposal.members:
            if not member.selected:
                continue
            county_name = (
                self.pipeline.county_1_name
                if int(member.county_index) == 1
                else self.pipeline.county_2_name
            )
            lines.append(
                f"{html.escape(str(county_name))} "
                f"{html.escape(str(member.road_name))} "
                f"{html.escape(str(member.address_summary))}"
            )
        self.address_label.setText("<br>".join(lines) if lines else "Addr: —")

    def _show_current_junction(self, *, redraw_controls: bool = True) -> None:
        if not self._junction_mode or self._item_count() == 0 or self._closing:
            return
        proposal = self._row()
        completed = sum(item.decision is not None for item in self.junctions)
        self.position_label.setText(
            f"Round {proposal.round_number} — Junction {self.current_index + 1} "
            f"of {len(self.junctions)}<br>"
            f"Completed {completed}; remaining {len(self.junctions) - completed}"
        )
        self.probability_label.setText(
            f"<b>Average pair probability:</b> {_format_probability(proposal.average_probability)}<br>"
            f"<b>Pair evidence:</b> {len(proposal.pair_keys)} pair(s)<br>"
            f"<b>Seed pair:</b> {html.escape(str(proposal.seed_pair_key))}<br>"
            f"<b>Junction point:</b> {proposal.junction_x:.3f}, {proposal.junction_y:.3f}"
        )
        self.reason_label.setText(
            "<b>Review action:</b><br>Toggle roads that should participate, then use "
            "<b>Drag Junction</b> (enabled by default) to move the green X. The selected "
            "road geometry previews update live. After using Pan/Zoom, select Drag "
            "Junction again before moving the X. Then Accept or Reject the junction."
        )
        self.policy_label.setText(self._review_policy_text())
        self.progress.setValue(completed)
        self.previous_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.junctions) - 1)

        if redraw_controls:
            self._clear_member_controls()
            # Insert checkboxes before the reset button, which is kept last.
            for member in proposal.members:
                box = QCheckBox(
                    f"{self.pipeline.county_1_name if member.county_index == 1 else self.pipeline.county_2_name} "
                    f"{member.road_id} — {member.road_name} [{member.attachment_type}]"
                )
                box.setChecked(bool(member.selected))
                box.toggled.connect(
                    lambda checked, key=member.key: self._junction_member_toggled(key, checked)
                )
                self.members_layout.insertWidget(self.members_layout.count() - 1, box)
                self._member_checkboxes[member.key] = box

        self._update_junction_status_labels(proposal)
        self._update_address_ranges(proposal)
        self.canvas.draw_junction(
            self.pipeline,
            proposal,
            include_basemap=self.basemap_checkbox.isChecked(),
        )
        self._apply_axis_label_visibility(self.axis_labels_checkbox.isChecked())
        self.legend_label.setText(
            f"<span style='color:#2f5cff;font-size:18px;'>━━━━</span> <b>{html.escape(str(self.pipeline.county_1_name))}</b><br>"
            f"<span style='color:#f0a128;font-size:18px;'>━━━━</span> <b>{html.escape(str(self.pipeline.county_2_name))}</b><br>"
            "<span style='color:#8a8a8a;font-size:18px;'>━━━━</span> Unchecked road<br>"
            "<span style='color:#32cd32;font-size:18px;'>✖</span> Draggable shared junction"
        )

    def _show_current(self) -> None:
        if self._junction_mode:
            self._show_current_junction()
            return
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
        self.address_label.setText(
            f"{html.escape(str(self.pipeline.county_1_name))} "
            f"{html.escape(str(row.get('full_road_label_county1') or row.get('road_name_county1') or row['county_1_id']))} "
            f"{html.escape(format_address_summary(row, 1))}<br>"
            f"{html.escape(str(self.pipeline.county_2_name))} "
            f"{html.escape(str(row.get('full_road_label_county2') or row.get('road_name_county2') or row['county_2_id']))} "
            f"{html.escape(format_address_summary(row, 2))}"
        )
        self.probability_label.setText(
            "<b>Geometric:</b> "
            + _format_probability(row.get("geometric_valid_pair_probability"))
            + "<br><b>Textual:</b> "
            + _format_probability(row.get("textual_valid_pair_probability"))
            + "<br><b>Combined:</b> "
            + _format_probability(row.get("probablity"))
            + "<br><b>Safe Reject threshold:</b> "
            + _format_probability(self.pipeline.optimized_threshold)
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
        self._apply_axis_label_visibility(
            self.axis_labels_checkbox.isChecked()
        )
        self.legend_label.setText(self._legend_html(road_name_1, road_name_2))

    def _apply_axis_label_visibility(self, visible: bool) -> None:
        """Toggle coordinate annotations and let the map reclaim their space."""
        axes = self.canvas.axes
        is_visible = bool(visible)

        axes.xaxis.label.set_visible(is_visible)
        axes.yaxis.label.set_visible(is_visible)
        axes.xaxis.get_offset_text().set_visible(is_visible)
        axes.yaxis.get_offset_text().set_visible(is_visible)
        axes.tick_params(
            axis="both",
            which="both",
            bottom=is_visible,
            left=is_visible,
            labelbottom=is_visible,
            labelleft=is_visible,
        )

        # The map figure already uses Matplotlib's tight-layout engine.
        # Recomputing it here expands the plot into the released left/bottom
        # margins, or restores those margins when the labels are enabled.
        try:
            self.canvas.figure.tight_layout()
        except (RuntimeError, ValueError):
            LOGGER.debug(
                "Matplotlib could not recompute the map layout immediately.",
                exc_info=True,
            )
        self.canvas.draw_idle()

    def _redraw(self) -> None:
        if self._item_count() > 0 and not self._changing_pair and not self._closing:
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
        if self.current_index < self._item_count() - 1:
            self.current_index += 1
            self._show_current()

    def _set_transition_controls_enabled(self, enabled: bool) -> None:
        self.no_button.setEnabled(enabled)
        self.yes_button.setEnabled(enabled)
        self.previous_button.setEnabled(enabled and self.current_index > 0)
        self.next_button.setEnabled(
            enabled and self.current_index < self._item_count() - 1
        )
        if hasattr(self, "reset_point_button"):
            self.reset_point_button.setEnabled(enabled)
        for checkbox in self._member_checkboxes.values():
            checkbox.setEnabled(enabled)
        self.basemap_checkbox.setEnabled(enabled)
        self.axis_labels_checkbox.setEnabled(enabled)
        self.close_button.setEnabled(enabled)
        self.quit_button.setEnabled(enabled)

    def _finish_pair_transition(self) -> None:
        try:
            self._show_current()
        except Exception:
            LOGGER.exception("Failed while displaying the next manual-review item.")
            QMessageBox.critical(
                self,
                "Could not display review item",
                "The decision is still in memory, but the next map could not be "
                "displayed. See the console log for the complete traceback.",
            )
        finally:
            self._changing_pair = False
            if not self._closing:
                self._set_transition_controls_enabled(True)

    def _record(self, decision: str) -> None:
        if self._item_count() == 0 or self._changing_pair or self._closing:
            return
        if self._junction_mode:
            self._record_junction(decision)
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

    def _record_junction(self, decision: str) -> None:
        self._changing_pair = True
        self._set_transition_controls_enabled(False)
        normalized = "accept" if str(decision).lower() == "yes" else "reject"
        proposal = self._row()
        self.saved_label.setText(
            f"Recording junction decision in memory: {normalized.upper()}..."
        )
        try:
            self.pipeline.record_junction_decision(
                proposal.signature,
                normalized,
                selected_member_keys=proposal.selected_member_keys,
                junction_x=proposal.junction_x,
                junction_y=proposal.junction_y,
            )
        except Exception as exc:
            LOGGER.exception("Could not record the junction decision in memory.")
            self._changing_pair = False
            self._set_transition_controls_enabled(True)
            QMessageBox.critical(self, "Could not record junction decision", str(exc))
            return

        pending_positions = [
            index for index, item in enumerate(self.junctions) if item.decision is None
        ]
        if not pending_positions:
            LOGGER.info(
                "Junction round completed in RAM. Closing review so the round can be committed."
            )
            self.saved_label.setText(
                "All junctions in this round reviewed. Applying the round automatically..."
            )
            self._changing_pair = False
            self.all_completed.emit()
            self.accept()
            return

        later = [position for position in pending_positions if position > self.current_index]
        self.current_index = int(later[0] if later else pending_positions[0])
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
