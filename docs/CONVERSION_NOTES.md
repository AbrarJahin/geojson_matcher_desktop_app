# Conversion notes

## Notebook sections retained unchanged

The compatibility kernel still executes the notebook #7 code for:

- loading, validating, and projecting the two datasets;
- road preparation and candidate generation;
- feature engineering and geometric/textual feature separation;
- the fixed two-cluster geometric GMM;
- the textual Beta latent model;
- two-model average preparation;
- Empirical-Bayes combined probability;
- optimized threshold and uncertainty;
- the 2%–30% hybrid manual-review plan;
- final decision composition, conflict resolution, endpoint connection, and final output creation.

`app/core/legacy_program.py` is byte-identical to the prior verified desktop package. Its SHA-256 remains recorded in `docs/ALGORITHM_MANIFEST.json`.

## Desktop-only behavior

- Manual decisions are changed in RAM on each Yes/No action.
- Closing the review dialog does not persist; one progress CSV is atomically replaced only when the whole application quits normally.
- Valid remembered input/output paths are analyzed automatically at startup, then a compatible session is restored before review.
- Compatible progress is selected before cell 36 runs, allowing cell 36's original loading logic to restore decisions.
- The analysis-time decision audit is routed to an in-memory file-like object; the durable audit is written during finalization.
- The review map uses Qt-native asynchronous HTTP tile requests rather than contextily/raster worker threads.
- Qt, Python, thread, and native-fault diagnostics are written to console and local log files.
