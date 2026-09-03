# Road Matcher Desktop

Road Matcher Desktop runs the retained analytical cells from notebook #7 inside a local PySide6 application. Candidate generation, feature engineering, the geometric model, textual model, Empirical-Bayes fusion, threshold selection, the 2%–30% manual-review plan, conflict handling, and final road-connection/output code remain in `app/core/legacy_program.py`.


## Manual-review selection and display order

Notebook #7 remains the authority for **which pairs are selected**. It calculates
the allowed integer review-count range as:

```text
minimum = ceil(total candidate pairs × 2%)
maximum = floor(total candidate pairs × 30%)
```

For tiny datasets where integer rounding makes both constraints impossible, the
notebook reviews one row. The notebook then selects rows by uncertainty and
geometric/textual model disagreement; it does **not** select rows simply because
they have the lowest raw combined probability.

The desktop application preserves that selected set exactly, validates the
2%-to-30% barrier a second time, and presents the selected rows in the requested
visual order: **lowest combined `probablity` first, then monotonically higher
combined probabilities**. Notebook rank and pair key are used only to make ties
deterministic. The selected count, selected percentage, and allowed integer range
are shown in the application and written to the console log.

## Review-state behavior

Manual Yes/No actions now follow a deliberate in-memory workflow:

1. Clicking **Yes** or **No** changes only the in-memory decision DataFrame.
2. No CSV is written during an individual button click.
3. Closing only the manual-review window keeps every decision in RAM and does not write a CSV.
4. **Save Session & Quit Application**, the main-window X button, or normal application shutdown writes the current session exactly once.
5. The save uses a same-directory temporary file followed by `os.replace`, so the previous session file is atomically replaced.
6. Older recovery/session files for the same input pair are deleted after the replacement succeeds.
7. The application keeps one canonical progress file:

```text
<output>\.road_matcher_state\<File1>__<File2>_manual_review_progress.csv
```

At startup, the application restores the most recent input/output selections but does not start analysis automatically. Click **Analyze GeoJSON Files** when you are ready to run the analytical pipeline. A previous state CSV is merged only when its input paths, SHA-256 input fingerprints, pipeline settings, and retained algorithm fingerprint are compatible. If no compatible state exists, review begins from the first selected pair.

Because decisions intentionally remain only in RAM until the whole application quits normally, an operating-system power loss, forced termination, or native crash before normal application shutdown can lose decisions made during that run.

## Crash-resistance changes

The review map no longer runs `contextily`, raster reprojection, GDAL/rasterio, or `QRunnable` work inside the application process. Nearby roads and selected roads are transformed only for display into Web Mercator. Optional OpenStreetMap tiles are requested asynchronously through Qt's `QNetworkAccessManager`, and all outstanding replies are aborted when the pair changes or the review window closes.

This removes the previous Qt worker/signal lifetime race and native raster-worker shutdown path. Scientific BLAS/OpenMP execution is also constrained to one native worker with `threadpoolctl`; this prevents repeated GMM runs from deadlocking inside the Qt process without changing the notebook formulas, random state, cell order, or model outputs. The analytical model still uses its original configured projected CRS and is not changed by the display transformation.

## Diagnostics

`make run` keeps logs visible in the console. Detailed logs are also written to:

```text
%LOCALAPPDATA%\RoadMatcher\logs\road-matcher.log
%LOCALAPPDATA%\RoadMatcher\native-crash.log
```

Logging includes:

- Python exceptions from the main thread and Python worker threads;
- Qt warnings, critical messages, and fatal messages;
- analysis/finalization stage messages;
- session load/save decisions;
- application and Qt event-loop shutdown;
- Python `faulthandler` traces for supported native faults.

If the child process exits abnormally, `make run` prints the unsigned Windows status code, decodes common failures such as `0xC0000005`, and prints the tail of both log files.

## Development setup

Use 64-bit Windows 10 or Windows 11 with Miniconda/Anaconda and GNU Make. Inno Setup 6 is needed only for the installer.

```text
make help
make doctor
make setup
make verify
make run
```

`make setup` creates a project-local Conda prefix at:

```text
RoadMatcherDesktop\.venv
```

No manual Conda activation is required. All commands execute through:

```text
conda run --prefix <project>/.venv ...
```

## Application workflow

1. Select two `.json` or `.geojson` road files.
2. Select the output folder used for final outputs and resumable state.
3. Confirm the road ID column, projected CRS, candidate distance, and batch size.
4. Click **Analyze GeoJSON Files** whenever you want to start analysis. Remembered paths are restored on later launches, but analysis does not start automatically.
5. If a compatible state CSV exists, its decisions are restored into RAM. Otherwise review begins with no completed decisions.
6. Review selected pairs with the Yes/No buttons or the **Y**/**N** keyboard shortcuts. The desktop queue starts with the lowest combined probability in the notebook-selected set and proceeds upward.
7. **Return to Main Window** closes only the review window; decisions continue to exist in RAM.
8. Press **Save Session & Quit Application** (or close the main window) to atomically replace the old saved session with the current RAM state.
9. When the last selected row is answered, the review window closes and final GeoJSON/audit creation starts automatically from the in-memory decisions. No separate final-output button is required. The review-session CSV is still updated only at application quit.

The two input files are alphabetically ordered before being assigned as County/File 1 and County/File 2, matching notebook #7.

## Output files

For `Alpha.geojson` and `Beta.geojson`, the output directory can contain:

```text
Alpha_updated.json
Beta_updated.json
Alpha__Beta_final_pair_decisions.csv
Alpha__Beta_decision_audit_<timestamp>.csv
Alpha__Beta_connection_audit_<timestamp>.csv
.road_matcher_state/
  Alpha__Beta_manual_review_progress.csv
```

The intermediate decision audit created by notebook cell 36 is held in memory during analysis. The durable audit CSV is written during finalization.

## Verification

Run:

```text
make verify
```

The suite checks:

- compilation of all retained notebook cells;
- exact 2%-to-30% integer-bound calculations and strict selected-count validation;
- lowest-to-highest combined-probability desktop review ordering;
- usable-screen review-window sizing and left-side details/legend layout;
- automatic finalization after the last manual decision;
- configuration validation;
- in-memory-only button decisions;
- atomic session replacement and deletion of older recovery files;
- rejection of stale sessions after input changes;
- restoration of a saved session in a second full analytical run;
- repeated off-screen map redraw and shutdown;
- cancellation of outstanding online tile requests;
- synthetic end-to-end analysis and final GeoJSON/audit creation;
- full Python compilation.

## Standalone application and installer

```text
make build
make installer
```

The standalone single-file application is written to:

```text
dist\RoadMatcher.exe
```

The installer is written to:

```text
installer_output\RoadMatcher-Setup-1.3.0.exe
```

The installer copies only `RoadMatcher.exe` into the application directory; it
does not install loose `.py`, `.pyc`, `.pyd`, Python DLL, or package directories.
When upgrading a legacy one-folder installation, it also removes that version's
`_internal` directory and any root-level `.pyd` or `python*.dll` runtime files.
The executable still contains the Python runtime and required native modules and
temporarily extracts them while running. This keeps the installed directory
clean and makes casual inspection harder, but it cannot guarantee prevention of
reverse engineering. The frozen GIS smoke test runs before installer creation
to reject a one-file build whose Qt, Pyogrio/GDAL, PyProj, or Shapely runtime is
incomplete.
