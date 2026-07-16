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

The Makefile delegates cross-platform process and filesystem work to `scripts/project_tasks.py`. This avoids PowerShell activation scripts and execution-policy requirements. It does not change the desktop application or matching algorithm.

No `.ps1` or `.bat` files are required or included. All development, testing, packaging, and installer operations are exposed through Make targets.

## Supported environment

Use 64-bit Windows 10 or Windows 11 and Python 3.12 for the simplest build path. Python is required only on the development/build computer. The generated standalone application includes the Python runtime for end users.

GNU Make must be installed and available as `make`. Some MinGW installations expose it as `mingw32-make`; when that is the case, replace `make` with `mingw32-make` in every command below.

## Setup and run from VS Code

Extract the project and open its root folder in VS Code. Run the following commands from the VS Code terminal:

```text
make setup
make run
```

`make setup` performs all of the following without activating the virtual environment:

- Creates `.venv`.
- Upgrades `pip`, `setuptools`, and `wheel`.
- Installs runtime, test, and packaging dependencies.
- Runs `pip check`.

The default Windows Python launcher is `py`. To use Python 3.12 explicitly:

```text
make setup PYTHON="py -3.12"
```

To use a `python` executable already available on `PATH`:

```text
make setup PYTHON=python
```

After setup, every target invokes `.venv` directly; no PowerShell activation or execution-policy change is required.

## Complete Make commands

```text
make help
make setup
make run
make test
make verify
make build
make installer
make installer-only
make clean
make distclean
make rebuild
```

| Command | Purpose |
|---|---|
| `make setup` | Create `.venv` and install all required dependencies. |
| `make run` | Start the PySide6 desktop application. |
| `make test` | Run the automated tests. |
| `make verify` | Run dependency, test, and Python compilation checks. |
| `make build` | Test and create the standalone application folder. |
| `make installer` | Build the application and create the Windows installer. |
| `make installer-only` | Create the installer from an existing standalone build. |
| `make clean` | Delete build output and Python cache directories. |
| `make distclean` | Run `clean` and also delete `.venv`. |
| `make rebuild` | Clean and rebuild the standalone application. |

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

Run:

```text
make build
```

The output is:

```text
dist\RoadMatcher\RoadMatcher.exe
```

The build is intentionally **one-folder**, not one-file. GIS libraries, Qt plugins, PROJ data, GDAL/raster components, and scientific Python packages are more reliable and faster to start in one-folder mode.

## Build the Windows installer

1. Install Inno Setup 6.
2. Run:

```text
make installer
```

The command runs tests, builds the standalone application, locates `ISCC.exe`, and compiles `installer\RoadMatcher.iss`.

If Inno Setup is installed in a nonstandard location, provide the compiler path:

```text
make installer ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
```

To compile the installer without rebuilding the application:

```text
make installer-only
```

The installer is written to:

```text
installer_output\RoadMatcher-Setup-1.0.0.exe
```

The installed application runs without a separate Python installation.

## Cleaning generated files

Remove build products and Python caches while preserving `.venv`, source code, selected GeoJSON files, and user output files:

```text
make clean
```

Also remove `.venv`:

```text
make distclean
```

## Basemap behavior

The map always displays both candidate roads, nearby context roads, contact points, current gap, and proposed midpoint. The street basemap is optional and requires internet access. If Google tiles fail, the application attempts an Esri fallback; if both fail, vector-road review continues normally.

## Verification

Run:

```text
make verify
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
