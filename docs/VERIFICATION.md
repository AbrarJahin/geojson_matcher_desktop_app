# Verification record

The updated Road Matcher Desktop 1.3.0 project was checked in the artifact environment with the following results:

- `app/core/legacy_program.py` retained its previously verified file SHA-256: `6bd3a86cccdcd84fca77c66271cb85b4a8884d82ad66a4c8685b9ad17ba90467`.
- The 13 executed analytical sources match the embedded notebook source exactly, except for the documented removal of `from IPython.display import display` because the desktop compatibility namespace supplies `display`.
- Notebook #7's selected manual-review set and uncertainty/disagreement ranking logic were not modified.
- Notebook #7's integer barrier was independently verified as `ceil(total × 2%)` through `floor(total × 30%)`, including its one-row tiny-dataset exception.
- The desktop now validates that notebook-selected count again and raises a runtime error if the selected count falls outside the allowed range.
- The desktop review presentation was verified as monotonically increasing by combined `probablity`; this changes presentation order only, not the notebook-selected set.
- Every retained notebook cell and every Python module compiled successfully.
- The automated suite completed with **21 passed tests**.
- Review metadata and the dynamic legend were verified in the left-side panel; the Matplotlib map contains no internal legend.
- The review dialog was maximized using the Qt screen's `availableGeometry`, which excludes the taskbar/work area, and retained a scrollable sidebar for smaller displays.
- The main-window `Create Final Outputs` button was removed.
- The final manual decision closes the review without an intermediate completion popup; finalization begins automatically after the modal review returns.
- Yes/No actions and review-window close actions remained RAM-only.
- Explicit application-quit saving atomically replaced the canonical progress CSV and removed older recovery/session CSV files.
- A changed input fingerprint caused a previous session to be rejected.
- A second full analytical run restored a compatible saved session and reached zero remaining reviews.
- Remembered valid input/output paths triggered automatic startup analysis both with and without an existing state file.
- Finalization produced both updated GeoJSON files and all final decision/connection audit CSVs.
- Repeated off-screen map redraw and shutdown completed without Python worker threads.
- Outstanding Qt network tile requests were cancelled safely during map shutdown.
- The deleted-Qt-signal regression was exercised and safely detached without interrupting pipeline work.
- An off-screen Qt application startup and controlled close returned exit code 0 with Qt and Python logging active.
- The review map contains no `QThreadPool`, `QRunnable`, contextily, rasterio, or GDAL tile-reprojection path.

The core verification commands were:

```text
python -m compileall -q app main.py scripts tests
python -m pytest -rA
```

## Platform limitation

The verification environment is Linux, not the target Windows installation. It validates Python behavior, notebook-source consistency, Qt off-screen lifecycle, review-count bounds, presentation ordering, session semantics, automatic finalization flow, repeated analytical execution, and end-to-end output generation. It cannot prove that every Windows display driver, Qt platform plugin, antivirus product, Conda installation, or machine-specific native library will never fail.

For a clean Windows verification, extract the project into a new directory and run:

```text
make setup
make verify
make run
```

If Windows still terminates the process, retain the complete console output and inspect:

```text
%LOCALAPPDATA%\RoadMatcher\logs\road-matcher.log
%LOCALAPPDATA%\RoadMatcher\native-crash.log
```
