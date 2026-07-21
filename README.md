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

The Makefile delegates process, test, build, and installer operations to `scripts/project_tasks.py`. The development environment is a **project-local Conda prefix** stored at `./.venv`. No PowerShell activation script, batch file, `py` launcher, or manually activated Conda environment is required.

## Required development tools

Use 64-bit Windows 10 or Windows 11 with:

- Miniconda or Anaconda, with `conda` available in the terminal.
- GNU Make, available as `make` or `mingw32-make`.
- Inno Setup 6 only when creating the final Windows installer.

Your existing Conda base environment may use Python 3.9. That is acceptable. `make setup` uses Conda itself to create a separate Python 3.12 environment inside this repository:

```text
RoadMatcherDesktop\.venv
```

The generated standalone application includes Python for end users.

## Setup and run from VS Code

Extract the project, open its root folder in VS Code, and run:

```text
make help
make doctor
make setup
make run
```

`make setup` performs these operations:

1. Uses the `conda` executable, not the `py` or `python` command on `PATH`.
2. Creates a Conda prefix at `./.venv` with Python 3.12 and pip.
3. Upgrades pip, setuptools, and wheel inside that prefix.
4. Installs `requirements-dev.txt` inside the local prefix.
5. Runs `pip check`.

Every subsequent command uses:

```text
conda run --prefix <project>/.venv ...
```

You do not need to run `conda activate`.

The Makefile automatically prefers `C:\tools\miniconda3\Scripts\conda.exe` when that file exists. If Conda is installed elsewhere or is not found, use:

```text
make setup CONDA="C:/tools/miniconda3/Scripts/conda.exe"
```

The same override can be supplied to any target:

```text
make run CONDA="C:/tools/miniconda3/Scripts/conda.exe"
```

## Complete Make commands

```text
make help
make doctor
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
| `make help` | Show available commands without invoking Python. |
| `make doctor` | Display Conda, environment-prefix, and Python diagnostics. |
| `make setup` | Create/update the local `.venv` Conda prefix and install dependencies. |
| `make run` | Start the PySide6 desktop application through the local prefix. |
| `make test` | Run the automated tests through the local prefix. |
| `make verify` | Run dependency, test, and Python compilation checks. |
| `make build` | Test and create the standalone application folder. |
| `make installer` | Build the application and create the Windows installer. |
| `make installer-only` | Create the installer from an existing standalone build. |
| `make clean` | Delete build output and project caches while preserving `.venv`. |
| `make distclean` | Run `clean` and remove the local `.venv` Conda prefix. |
| `make rebuild` | Clean, test, and rebuild the standalone application. |

## VS Code interpreter

After `make setup`, select this interpreter in VS Code:

```text
<project>\.venv\python.exe
```

The application still runs through `make run`; selecting the interpreter improves editing, linting, and debugging in VS Code.

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

Install Inno Setup 6, then run:

```text
make installer
```

The command runs tests, creates the standalone application, locates `ISCC.exe`, and compiles `installer\RoadMatcher.iss`.

For a nonstandard Inno Setup location:

```text
make installer ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
```

To compile the installer without rebuilding:

```text
make installer-only
```

The installer is written to:

```text
installer_output\RoadMatcher-Setup-1.0.0.exe
```

The installed application runs without a separate Python or Conda installation.

## Cleaning generated files

Preserve the local Conda environment but remove build products and source-tree caches:

```text
make clean
```

Remove build products and the complete local Conda environment:

```text
make distclean
```

Recreate it later with `make setup`.

## Environment definition and source control

`environment.yml` records the required Conda base environment: Python 3.12 and pip. The machine-specific local prefix is supplied by the Makefile and is intentionally not embedded in the YAML file.

The root `.gitignore` contains:

```text
/.venv/
```

Therefore, the large local Conda environment will not be committed to Git.

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

## Manual-review button reliability

The desktop review window saves every Yes/No choice immediately. The save path is
`<output folder>/.road_matcher_state/`.

This version includes the following safeguards:

- online basemap tiles load on a worker thread, so slow internet does not freeze the UI;
- decision buttons are temporarily disabled during pair transitions to prevent duplicate clicks;
- manual decisions are stored with an explicit string-compatible dtype;
- progress files are written atomically with retries;
- if Windows, Excel, OneDrive, or antivirus locks the normal progress CSV, the app writes a timestamped recovery CSV and continues;
- the selected output and state folders are tested for write access before analysis starts.

If a permission dialog still appears, close any progress/final CSV opened in Excel and
select a writable output folder under Documents or Desktop rather than Program Files.
