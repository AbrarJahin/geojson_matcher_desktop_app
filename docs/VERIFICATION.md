# Verification record

The updated Road Matcher Desktop 1.2.0 project was checked in the artifact environment with the following results:

- `app/core/legacy_program.py` retained its previously verified file SHA-256: `6bd3a86cccdcd84fca77c66271cb85b4a8884d82ad66a4c8685b9ad17ba90467`.
- The 13 executed analytical sources match the embedded notebook source exactly, except for the documented removal of `from IPython.display import display` because the desktop compatibility namespace supplies `display`.
- Every retained notebook cell and every Python module compiled successfully.
- The automated suite completed with **14 passed tests**.
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
- A full off-screen GUI startup restored remembered paths, automatically ran all 13 analytical stages in a `QThread`, returned control to Qt, and wrote the single canonical state CSV during application quit.
- The review map contains no `QThreadPool`, `QRunnable`, contextily, rasterio, or GDAL tile-reprojection path.

The core verification commands were:

```text
python -m compileall -q app main.py scripts tests
python -m pytest -rA
```

## Platform limitation

The verification environment is Linux, not the target Windows installation. It validates Python behavior, notebook-source consistency, Qt off-screen lifecycle, session semantics, automatic resume behavior, repeated analytical execution, and end-to-end output generation. It cannot prove that every Windows display driver, Qt platform plugin, antivirus product, Conda installation, or machine-specific native library will never fail.

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
