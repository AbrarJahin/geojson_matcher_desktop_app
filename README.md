# Road Matcher Desktop

This project converts the supplied Colab notebook into a local PySide6 desktop application while retaining its road-pair candidate generation, geometric model, textual model, Empirical-Bayes fusion, optimized threshold, 2%-30% manual-review policy, one-to-one conflict handling, and midpoint connection behavior.

## Implemented desktop changes

1. Select two `.json` or `.geojson` road files with native Windows file dialogs.
2. Store resumable manual-review progress locally under the selected output folder in `.road_matcher_state`.
3. Write the two updated GeoJSON files and all decision/connection audit CSV files to the selected local output folder.
4. Review uncertain road pairs in an embedded interactive map. Use the mouse wheel to zoom and left-drag to pan. The toolbar also provides Home, Back, Forward, Pan, Zoom, and Save controls.
5. Use **Y** or **N** keyboard shortcuts, or the visible Yes/No buttons. Every answer is saved immediately.

## Architecture

The original validated analytical cells are retained in `app/core/legacy_program.py` and executed in a controlled compatibility namespace. The PySide6 UI and service wrapper replace only notebook/Colab concerns. This migration strategy minimizes numerical and decision-flow changes.

## Supported environment

Use 64-bit Windows 10 or Windows 11 and Python 3.12 for the simplest build path. Python is required only on the development/build computer. The generated standalone application includes the Python runtime for end users.

## Run from VS Code on Windows

Open the extracted project folder in VS Code, then run these commands in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.un.ps1
```

Equivalent manual commands:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-dev.txt
python main.py
```

## Application workflow

1. Select the two road GeoJSON files.
2. Select an output folder.
3. Confirm the stable road ID field, normally `OBJECTID`.
4. Click **Analyze GeoJSON Files**.
5. Review every selected uncertain/QC pair. Choose Yes to connect or No to reject.
6. After the final answer, the application creates both updated GeoJSON files and the audit CSVs.

The notebook's deterministic filename ordering is preserved: the two selected files are alphabetically ordered before being treated as County/File 1 and County/File 2.

## Local output files

For input files `Alpha.geojson` and `Beta.geojson`, output includes:

```text
Alpha_updated.json
Beta_updated.json
Alpha__Beta_final_pair_decisions.csv
Alpha__Beta_decision_audit_<timestamp>.csv
Alpha__Beta_connection_audit_<timestamp>.csv
.road_matcher_state/
  Alpha__Beta_manual_review_progress.csv
```

Do not delete the progress CSV until the review is complete if you want to resume after closing the application.

## Build the standalone Windows application

```powershell
.uild.ps1
```

The output is:

```text
dist\RoadMatcher\RoadMatcher.exe
```

The build is intentionally **one-folder**, not one-file. GIS libraries, Qt plugins, PROJ data, GDAL/raster components, and scientific Python packages are more reliable and faster to start in one-folder mode.

## Build the Windows installer

1. Install Inno Setup 6.
2. Run `build.ps1` first.
3. Open `installer\RoadMatcher.iss` in Inno Setup.
4. Click **Compile**.

The installer will be written to:

```text
installer_output\RoadMatcher-Setup-1.0.0.exe
```

The installed application runs without a separate Python installation.

## Basemap behavior

The map always displays both candidate roads, nearby context roads, contact points, current gap, and proposed midpoint. The street basemap is optional and requires internet access. If Google tiles fail, the application attempts an Esri fallback; if both fail, vector-road review continues normally.

## Verification

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests compile all retained notebook cells, validate configuration behavior, and run the analytical pipeline against generated synthetic GeoJSON files.

## Important assumptions retained from the notebook

- Input geometry is road `LineString` or `MultiLineString` data.
- Missing CRS is treated as `EPSG:4326`.
- The default projected CRS is `EPSG:26916`, appropriate for the original Indiana datasets.
- The stable ID column defaults to `OBJECTID` and must be present, non-null, and unique in both files.
- The minimum and maximum manual-review fractions remain 2% and 30%.
- Final GeoJSON preserves original FeatureCollection properties and changes only accepted road geometries.

## Licensing note

PySide6/Qt licensing obligations depend on distribution and procurement circumstances. Review Qt's applicable open-source or commercial license terms before external deployment, especially for government distribution.
