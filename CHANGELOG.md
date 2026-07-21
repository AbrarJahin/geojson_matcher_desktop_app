# Changelog

## [1.3.0] - 2026-07-21

### Verified

- Confirmed notebook #7 calculates the integer manual-review barrier as:
  - minimum: `ceil(total candidate pairs × 2%)`;
  - maximum: `floor(total candidate pairs × 30%)`;
  - one-row exception for tiny datasets where both rounded limits cannot be satisfied simultaneously.
- Confirmed notebook #7 raises a runtime error if the selected count falls outside that barrier.
- Added an independent desktop-side validation that repeats the same calculation and fails loudly if the notebook-selected count is outside the allowed range.
- Added visible and console diagnostics showing total candidates, selected count, selected percentage, and allowed integer range.

### Changed

- Preserved notebook #7's original selected manual-review set without changing analytical cells or model behavior.
- Changed only the desktop presentation order of selected rows to strictly ascending combined `probablity`:
  - lowest combined probability first;
  - incrementally higher combined probabilities afterward;
  - notebook review rank and pair key are deterministic tie-breakers.
- Moved review metadata from the top of the map into a dedicated left-side details panel.
- Moved the dynamic map legend from inside the Matplotlib map into the left-side panel.
- Enlarged the usable map region by placing the details panel and map in a resizable horizontal splitter.
- Maximized the manual-review dialog through Qt/Windows using the screen's `availableGeometry`, which excludes the Windows taskbar/work-area reservation.
- Added a scrollable sidebar so details and controls remain accessible on smaller displays without making the window taller than the usable screen.
- Removed the `Create Final Outputs` button from the main window.
- Removed the intermediate manual-review-complete popup.
- Final GeoJSON and audit output creation now starts automatically as soon as the final required manual decision is recorded.
- Kept Yes/No decisions in RAM and retained the existing save-session-on-application-quit behavior.

### Unchanged

- Candidate generation.
- Feature engineering.
- Geometric model.
- Textual model.
- Empirical-Bayes fusion.
- Unsupervised threshold calculation.
- Notebook #7's uncertainty/disagreement-based selection logic.
- Conflict resolution and road-connection logic.
- Embedded notebook and retained analytical source code.

### Tests

- Added tests for the exact 2%-to-30% integer bounds.
- Added tests that reject out-of-range selected counts.
- Added tests that the desktop review queue is monotonically ordered from lowest to highest combined probability.
- Added tests for usable-screen sizing, left-side legend placement, removal of the map legend, removal of the manual-completion popup, and removal of the manual finalization button.
