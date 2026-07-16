# Conversion notes

## Notebook sections retained

The compatibility kernel executes notebook code corresponding to:

- load, validate, and project datasets;
- road preparation and candidate generation;
- feature engineering and geometric/textual feature split;
- fixed two-cluster geometric GMM;
- textual Beta latent model;
- two-model average preparation;
- Empirical-Bayes combined probability;
- unsupervised optimized threshold and uncertainty;
- 2%-30% hybrid manual-review decision plan;
- progress persistence, conflict resolution, midpoint connection, and final output.

## Notebook sections replaced

- `%pip` installation cells;
- Google Drive mounting and Colab file upload;
- IPython display and synchronous `input()` review loop;
- `plt.show()` notebook map;
- `files.download()` browser download.

These are replaced by native file dialogs, local paths, background workers, the embedded interactive map, immediate CSV progress persistence, and local output files.
