# Verification record

The application source and retained notebook logic were not changed for this Conda-prefix update.

The project was checked in the artifact environment with these results:

- Python compilation: all project modules compiled successfully.
- Retained notebook cells: all retained cells compiled successfully.
- Automated application tests: passed.
- Synthetic analytical smoke test: candidate generation, geometric scoring, textual scoring, Empirical-Bayes fusion, optimized threshold, and manual-review queue creation completed.
- Manual progress test: decisions were written to the local `.road_matcher_state` CSV.
- Finalization test: both updated GeoJSON files and all audit CSVs were created.
- GUI startup and interactive-map tests had previously passed using Qt's off-screen platform.
- Make help is shell-only and does not invoke `py` or the system `python` command.
- The Makefile and task runner now use a local Conda prefix at `./.venv`.
- `.gitignore` excludes the root `/.venv/` directory.
- No `.ps1` or `.bat` files are included.

## Platform limitation

This artifact environment does not contain the user's Windows Conda installation or Inno Setup. Consequently, creation of the Windows-local Conda prefix, the Windows PyInstaller executable, and the Inno Setup installer must be executed on the user's Windows computer with `make setup`, `make build`, and `make installer`.
