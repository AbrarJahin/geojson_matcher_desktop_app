from __future__ import annotations

import builtins
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import time
import warnings

# Keep scientific native libraries single-threaded inside the Qt process.
# This prevents repeated GMM/BLAS runs from deadlocking or competing with the
# GUI/event threads on Windows while preserving deterministic calculations.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyproj import Transformer
from rapidfuzz import fuzz
from scipy.special import betaln, logsumexp
from shapely.geometry import LineString, MultiLineString, Point, mapping
from shapely.ops import nearest_points
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from app.core.legacy_program import CELLS, execute_legacy_pipeline

LOGGER = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
StageCallback = Callable[[int, int, str], None]

VALID_EXTENSIONS = {".json", ".geojson"}
SESSION_SCHEMA_VERSION = 2


class _InMemoryCsvBuffer(io.StringIO):
    """Discard notebook audit output during analysis while preserving cell behavior."""

    def __str__(self) -> str:
        return "<in-memory decision audit>"


def county_name_from_filename(path_or_name: str | Path) -> str:
    stem = Path(path_or_name).stem
    stem = re.sub(r"(_County)?_Boundary.*$", "", stem, flags=re.IGNORECASE)
    stem = stem.strip("_ ").strip()
    if not stem:
        raise ValueError(f"Could not derive a readable name from {path_or_name!r}.")
    return stem


def safe_run_key_part(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _legacy_program_sha256() -> str:
    digest = hashlib.sha256()
    for filename, source in CELLS:
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    county_file_1: Path
    county_file_2: Path
    output_dir: Path
    buffer_distance_meters: float = 50.0
    target_crs: str = "EPSG:26916"
    latlon_crs: str = "EPSG:4326"
    road_id_column: str = "OBJECTID"
    min_manual_review_fraction: float = 0.02
    max_manual_review_fraction: float = 0.30
    max_manual_batch_size: int = 30
    random_state: int = 42

    def normalized(self) -> "PipelineConfig":
        files = sorted(
            [
                self.county_file_1.expanduser().resolve(),
                self.county_file_2.expanduser().resolve(),
            ],
            key=lambda path: path.name.lower(),
        )
        output = self.output_dir.expanduser().resolve()
        return PipelineConfig(
            county_file_1=files[0],
            county_file_2=files[1],
            output_dir=output,
            buffer_distance_meters=float(self.buffer_distance_meters),
            target_crs=self.target_crs.strip(),
            latlon_crs=self.latlon_crs.strip(),
            road_id_column=self.road_id_column.strip(),
            min_manual_review_fraction=float(self.min_manual_review_fraction),
            max_manual_review_fraction=float(self.max_manual_review_fraction),
            max_manual_batch_size=int(self.max_manual_batch_size),
            random_state=int(self.random_state),
        )

    def validate(self) -> None:
        if self.county_file_1 == self.county_file_2:
            raise ValueError("Select two different GeoJSON files.")
        for path in (self.county_file_1, self.county_file_2):
            if not path.is_file():
                raise FileNotFoundError(f"Input file does not exist: {path}")
            if path.suffix.lower() not in VALID_EXTENSIONS:
                raise ValueError(f"Input must be .json or .geojson: {path.name}")
        if not self.output_dir:
            raise ValueError("Select an output directory.")
        if self.buffer_distance_meters <= 0:
            raise ValueError("Candidate distance must be greater than zero.")
        if not self.road_id_column:
            raise ValueError("Road ID column cannot be empty.")
        if not 0 < self.min_manual_review_fraction <= self.max_manual_review_fraction <= 1:
            raise ValueError(
                "Manual-review fractions must satisfy 0 < minimum <= maximum <= 1."
            )
        if self.max_manual_batch_size < 1:
            raise ValueError("Manual batch size must be at least 1.")


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    county_1_output: Path
    county_2_output: Path
    final_decisions_csv: Path
    decision_audit_csv: Path
    connection_audit_csv: Path
    accepted_pairs: int
    rejected_pairs: int


class RoadMatchingPipeline:
    """Desktop wrapper around the notebook's retained analytical pipeline.

    Manual decisions are changed only in the in-memory decision DataFrame. A
    single durable session CSV is atomically replaced when ``save_session`` is
    explicitly called by the review dialog or application shutdown flow.
    """

    def __init__(self, config: PipelineConfig, logger: LogCallback | None = None):
        self.config = config.normalized()
        self.config.validate()
        self._logger = logger or (lambda message: None)
        self.namespace: dict[str, Any] = {}
        self._analysis_complete = False
        self._session_dirty = False
        self._session_signature = ""
        self._source_sha256: tuple[str, str] = ("", "")
        self._pair_run_key = ""
        self._state_dir = self.config.output_dir / ".road_matcher_state"
        self._session_csv_path = self._state_dir / "manual_review_progress.csv"
        self._loaded_session_path: Path | None = None
        self._decision_audit_output: Path | None = None

    def set_logger(self, logger: LogCallback | None) -> None:
        self._logger = logger or (lambda message: None)

    def log(self, message: Any) -> None:
        text = str(message).rstrip()
        if not text:
            return
        LOGGER.info("%s", text)
        try:
            self._logger(text)
        except Exception:
            LOGGER.exception("The configured UI logger failed and was detached.")
            self._logger = lambda message: None

    def _print(self, *values: Any, sep: str = " ", end: str = "\n", **_: Any) -> None:
        text = sep.join(str(value) for value in values)
        if end and end != "\n":
            text += end
        self.log(text)

    def _display(self, value: Any = None, *_: Any, **__: Any) -> Any:
        if isinstance(value, pd.DataFrame):
            self.log(f"DataFrame: {len(value)} rows × {len(value.columns)} columns")
        elif value is not None:
            self.log(value)
        return value

    @staticmethod
    def _assert_directory_writable(directory: Path, label: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".road_matcher_write_test_{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
        except OSError as exc:
            raise PermissionError(
                f"{label} is not writable: {directory}\n"
                "Choose a normal user folder such as Documents or Desktop, "
                "and do not use Program Files or a read-only/network location."
            ) from exc
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def _build_session_identity(self) -> None:
        cfg = self.config
        self._source_sha256 = (
            _sha256_file(cfg.county_file_1),
            _sha256_file(cfg.county_file_2),
        )
        payload = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "source_file_1": str(cfg.county_file_1),
            "source_file_2": str(cfg.county_file_2),
            "source_sha256_1": self._source_sha256[0],
            "source_sha256_2": self._source_sha256[1],
            "buffer_distance_meters": cfg.buffer_distance_meters,
            "target_crs": cfg.target_crs,
            "latlon_crs": cfg.latlon_crs,
            "road_id_column": cfg.road_id_column,
            "min_manual_review_fraction": cfg.min_manual_review_fraction,
            "max_manual_review_fraction": cfg.max_manual_review_fraction,
            "max_manual_batch_size": cfg.max_manual_batch_size,
            "random_state": cfg.random_state,
            "legacy_program_sha256": _legacy_program_sha256(),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._session_signature = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _session_candidates(self) -> list[Path]:
        pattern = f"{self._pair_run_key}_manual_review_progress*.csv"
        candidates = list(self._state_dir.glob(pattern))
        if self._session_csv_path not in candidates:
            candidates.append(self._session_csv_path)
        return [path for path in candidates if path.is_file()]

    def _progress_file_is_compatible(self, path: Path) -> bool:
        try:
            progress = pd.read_csv(path, dtype=str)
        except Exception:
            LOGGER.exception("Ignoring unreadable session file: %s", path)
            return False

        required = {
            "pair_key",
            "manual_decision",
            "source_file_1",
            "source_file_2",
        }
        if not required.issubset(progress.columns):
            self.log(f"Ignoring incompatible session file without required columns: {path}")
            return False

        if progress.empty:
            return False

        source_1 = progress["source_file_1"].dropna().astype(str)
        source_2 = progress["source_file_2"].dropna().astype(str)
        if source_1.empty or source_2.empty:
            return False
        if not source_1.eq(str(self.config.county_file_1)).all():
            return False
        if not source_2.eq(str(self.config.county_file_2)).all():
            return False

        if "session_signature" in progress.columns:
            signatures = progress["session_signature"].dropna().astype(str)
            if not signatures.empty and not signatures.eq(self._session_signature).all():
                self.log(
                    "Ignoring a saved session because the input files, settings, or "
                    f"algorithm changed: {path}"
                )
                return False
        else:
            self.log(f"Loading legacy progress file with path-only validation: {path}")

        decisions = (
            progress["manual_decision"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.lower()
        )
        if not decisions.isin(["yes", "no"]).all():
            self.log(f"Ignoring a session file containing invalid decisions: {path}")
            return False
        return True

    def _find_session_to_load(self) -> Path | None:
        compatible = [
            path for path in self._session_candidates() if self._progress_file_is_compatible(path)
        ]
        if not compatible:
            return None
        return max(compatible, key=lambda path: path.stat().st_mtime_ns)

    def _manual_progress_frame(self) -> pd.DataFrame:
        frame = self.decision_df
        manual_rows = frame.loc[
            frame["requires_manual_verification"],
            [
                "pair_key",
                "county_1_id",
                "county_2_id",
                "manual_pair_number",
                "manual_batch_id",
                "manual_decision",
            ],
        ].copy()
        saved_at = datetime.now().isoformat(timespec="seconds")
        manual_rows["source_file_1"] = str(self.config.county_file_1)
        manual_rows["source_file_2"] = str(self.config.county_file_2)
        manual_rows["source_sha256_1"] = self._source_sha256[0]
        manual_rows["source_sha256_2"] = self._source_sha256[1]
        manual_rows["session_signature"] = self._session_signature
        manual_rows["session_schema_version"] = SESSION_SCHEMA_VERSION
        manual_rows["saved_at"] = saved_at
        return manual_rows

    @staticmethod
    def _atomic_replace_csv(frame: pd.DataFrame, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(
            f".{target.name}.{os.getpid()}.{datetime.now().strftime('%H%M%S%f')}.tmp"
        )
        try:
            frame.to_csv(temp, index=False)
            with temp.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())

            last_error: OSError | None = None
            for delay in (0.0, 0.05, 0.15, 0.30, 0.60):
                if delay:
                    time.sleep(delay)
                try:
                    os.replace(temp, target)
                    return
                except OSError as exc:
                    last_error = exc
            raise PermissionError(
                "The saved-session file could not be replaced. Close it in Excel "
                f"or another program and try again: {target}"
            ) from last_error
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _remove_old_session_files(self) -> None:
        for path in self._session_candidates():
            if path == self._session_csv_path:
                continue
            try:
                path.unlink(missing_ok=True)
                self.log(f"Removed older session file: {path}")
            except OSError:
                LOGGER.exception("Could not remove old session file: %s", path)

        for temp in self._state_dir.glob(
            f".{self._session_csv_path.name}.*.tmp"
        ):
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                LOGGER.exception("Could not remove stale temporary session file: %s", temp)

    def save_session(self, *, force: bool = False, reason: str = "application quit") -> Path | None:
        """Atomically persist the current RAM decisions as the only session CSV."""
        if not self._analysis_complete:
            return None
        if not force and not self._session_dirty and self._session_csv_path.is_file():
            return self._session_csv_path

        frame = self._manual_progress_frame()
        self._atomic_replace_csv(frame, self._session_csv_path)
        self._remove_old_session_files()
        self.namespace["MANUAL_PROGRESS_CSV"] = self._session_csv_path
        self._loaded_session_path = self._session_csv_path
        self._session_dirty = False
        completed = int(frame["manual_decision"].notna().sum())
        self.log(
            f"Saved {completed} manual decision(s) during {reason}: "
            f"{self._session_csv_path}"
        )
        return self._session_csv_path

    def _ram_only_progress_stub(self) -> None:
        """Notebook compatibility hook: desktop review persists only on quit."""
        self.log("Manual decisions remain in RAM until the review/app is closed.")

    def _build_namespace(self) -> dict[str, Any]:
        cfg = self.config
        output_dir = cfg.output_dir
        self._state_dir = output_dir / ".road_matcher_state"
        self._assert_directory_writable(output_dir, "Output folder")
        self._assert_directory_writable(self._state_dir, "Manual-review state folder")

        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._pair_run_key = (
            f"{safe_run_key_part(cfg.county_file_1.stem)}"
            f"__{safe_run_key_part(cfg.county_file_2.stem)}"
        )
        self._session_csv_path = (
            self._state_dir / f"{self._pair_run_key}_manual_review_progress.csv"
        )
        self._build_session_identity()
        self._loaded_session_path = self._find_session_to_load()
        progress_input = self._loaded_session_path or self._session_csv_path
        self._decision_audit_output = (
            output_dir / f"{self._pair_run_key}_decision_audit_{run_timestamp}.csv"
        )

        namespace: dict[str, Any] = {
            "__builtins__": builtins.__dict__,
            "__name__": "road_matcher_notebook_compatibility_kernel",
            "os": os,
            "re": re,
            "json": json,
            "math": math,
            "shutil": shutil,
            "warnings": warnings,
            "Path": Path,
            "datetime": datetime,
            "Optional": Optional,
            "np": np,
            "pd": pd,
            "gpd": gpd,
            "plt": plt,
            "Point": Point,
            "LineString": LineString,
            "MultiLineString": MultiLineString,
            "mapping": mapping,
            "nearest_points": nearest_points,
            "fuzz": fuzz,
            "Transformer": Transformer,
            "betaln": betaln,
            "logsumexp": logsumexp,
            "StandardScaler": StandardScaler,
            "PCA": PCA,
            "GaussianMixture": GaussianMixture,
            "display": self._display,
            "clear_output": lambda *args, **kwargs: None,
            "print": self._print,
            "BUFFER_DISTANCE_METERS": cfg.buffer_distance_meters,
            "TARGET_CRS": cfg.target_crs,
            "LATLON_CRS": cfg.latlon_crs,
            "ROAD_ID_COL": cfg.road_id_column,
            "PCA_COLUMN_NO": 3,
            "RANDOM_STATE": cfg.random_state,
            "GMM_MIN_COMPONENTS": 2,
            "GMM_MAX_COMPONENTS": 6,
            "GMM_COVARIANCE_TYPES": ["full", "diag", "tied", "spherical"],
            "MIN_MANUAL_REVIEW_FRACTION": cfg.min_manual_review_fraction,
            "MAX_MANUAL_REVIEW_FRACTION": cfg.max_manual_review_fraction,
            "MAX_MANUAL_BATCH_SIZE": cfg.max_manual_batch_size,
            "COUNTY_FILE_1": str(cfg.county_file_1),
            "COUNTY_FILE_2": str(cfg.county_file_2),
            "COUNTY_1_NAME": county_name_from_filename(cfg.county_file_1),
            "COUNTY_2_NAME": county_name_from_filename(cfg.county_file_2),
            "MARION_FILE": str(cfg.county_file_1),
            "HAMILTON_FILE": str(cfg.county_file_2),
            "OUTPUT_DIR": output_dir,
            "STATE_DIR": self._state_dir,
            "RUN_TIMESTAMP": run_timestamp,
            "PAIR_RUN_KEY": self._pair_run_key,
            "MANUAL_PROGRESS_CSV": progress_input,
            # Cell 36 still executes its to_csv statement, but the desktop keeps
            # that intermediate audit in memory until finalization.
            "DECISION_AUDIT_CSV": _InMemoryCsvBuffer(),
            "SECTION_24_OUTPUT_CSV": output_dir
            / f"{self._pair_run_key}_final_pair_decisions.csv",
            "CONNECTION_AUDIT_CSV": output_dir
            / f"{self._pair_run_key}_connection_audit_{run_timestamp}.csv",
        }
        return namespace

    def run_analysis(
        self, stage_callback: StageCallback | None = None
    ) -> "RoadMatchingPipeline":
        self.namespace = self._build_namespace()
        self.log(f"Input 1: {self.config.county_file_1}")
        self.log(f"Input 2: {self.config.county_file_2}")
        self.log(f"Output directory: {self.config.output_dir}")
        if self._loaded_session_path is None:
            self.log("No compatible saved session was found; starting review from the beginning.")
        else:
            self.log(f"Compatible saved session found: {self._loaded_session_path}")
        self.log("Running retained notebook analysis pipeline...")
        # Limit BLAS/OpenMP-backed estimators to one native worker. The notebook
        # calculations and ordering are unchanged; only native parallelism is
        # constrained to keep repeated analysis runs stable in a Qt process.
        with threadpool_limits(limits=1):
            execute_legacy_pipeline(self.namespace, stage_callback=stage_callback)

        decision_frame = self.namespace["section_24_decision_df"]
        decision_frame["manual_decision"] = decision_frame["manual_decision"].astype(
            "object"
        )
        normalized = (
            decision_frame["manual_decision"]
            .astype(str)
            .str.strip()
            .str.lower()
        )
        invalid = decision_frame["manual_decision"].notna() & ~normalized.isin(
            ["yes", "no"]
        )
        decision_frame.loc[invalid, "manual_decision"] = pd.NA
        self.namespace["section_24_decision_df"] = decision_frame
        # Ensure no notebook helper can write progress during a Yes/No action.
        self.namespace["_save_manual_progress"] = self._ram_only_progress_stub
        # Future explicit saves always replace the canonical file, even when a
        # legacy/recovery file was used as the input during this run.
        self.namespace["MANUAL_PROGRESS_CSV"] = self._session_csv_path
        self._analysis_complete = True
        self._session_dirty = False

        completed = int(
            decision_frame.loc[
                decision_frame["requires_manual_verification"], "manual_decision"
            ].notna().sum()
        )
        if completed:
            self.log(f"Restored {completed} completed manual decision(s) into RAM.")
        diagnostics = self.manual_review_diagnostics()
        self.log(
            "Manual-review barrier verified: "
            f"selected {diagnostics['selected']} of {diagnostics['total_candidates']} "
            f"({diagnostics['selected_fraction']:.2%}); allowed integer range "
            f"{diagnostics['minimum']}..{diagnostics['maximum']} "
            f"({diagnostics['minimum_fraction']:.0%}.."
            f"{diagnostics['maximum_fraction']:.0%})."
        )
        self.log(
            "Desktop review presentation order verified: lowest combined probability "
            "to highest within the unchanged notebook-selected queue."
        )
        self.log("Analysis pipeline completed.")
        return self

    def _require_analysis(self) -> None:
        if not self._analysis_complete:
            raise RuntimeError("Run analysis before accessing pipeline results.")

    @property
    def decision_df(self) -> pd.DataFrame:
        self._require_analysis()
        return self.namespace["section_24_decision_df"]

    @property
    def features_df(self) -> pd.DataFrame:
        self._require_analysis()
        return self.namespace["features_df"]

    @property
    def county_1_name(self) -> str:
        return self.namespace.get(
            "COUNTY_1_NAME", county_name_from_filename(self.config.county_file_1)
        )

    @property
    def county_2_name(self) -> str:
        return self.namespace.get(
            "COUNTY_2_NAME", county_name_from_filename(self.config.county_file_2)
        )

    @property
    def optimized_threshold(self) -> float:
        self._require_analysis()
        return float(self.namespace["OPTIMISED_THRESHOLD"])

    @property
    def has_unsaved_decisions(self) -> bool:
        return bool(self._analysis_complete and self._session_dirty)

    @property
    def session_path(self) -> Path:
        return self._session_csv_path

    @property
    def loaded_session_path(self) -> Path | None:
        return self._loaded_session_path

    @staticmethod
    def _manual_review_bounds_for_total(
        total_pairs: int, minimum_fraction: float, maximum_fraction: float
    ) -> tuple[int, int]:
        """Mirror notebook #7's integer 2%-to-30% review bounds exactly."""
        total_pairs = int(total_pairs)
        if total_pairs < 0:
            raise ValueError("total_pairs cannot be negative.")
        if total_pairs == 0:
            return 0, 0

        minimum = max(1, int(math.ceil(total_pairs * float(minimum_fraction))))
        maximum = max(1, int(math.floor(total_pairs * float(maximum_fraction))))
        if minimum > maximum:
            # Same tiny-dataset exception used by notebook #7.
            minimum = maximum = 1
        return minimum, maximum

    def manual_review_diagnostics(self) -> dict[str, int | float | bool]:
        """Report and strictly validate the notebook's review-count barrier."""
        frame = self.decision_df
        total = int(len(frame))
        selected = int(frame["requires_manual_verification"].astype(bool).sum())
        minimum, maximum = self._manual_review_bounds_for_total(
            total,
            self.config.min_manual_review_fraction,
            self.config.max_manual_review_fraction,
        )
        within_bounds = minimum <= selected <= maximum if total else selected == 0
        if not within_bounds:
            raise RuntimeError(
                "Manual-review count is outside notebook #7's configured barrier: "
                f"selected={selected}, allowed={minimum}..{maximum}, total={total}."
            )
        return {
            "total_candidates": total,
            "selected": selected,
            "minimum": minimum,
            "maximum": maximum,
            "selected_fraction": (selected / total) if total else 0.0,
            "minimum_fraction": float(self.config.min_manual_review_fraction),
            "maximum_fraction": float(self.config.max_manual_review_fraction),
            "within_bounds": within_bounds,
        }

    def review_rows(self, include_completed: bool = True) -> pd.DataFrame:
        """Return notebook-selected rows in desktop review order.

        Notebook #7 still decides *which* pairs are selected. The desktop presents
        that unchanged selected set from the lowest combined ``probablity`` to the
        highest, with notebook rank and pair key used only as deterministic ties.
        """
        frame = self.decision_df
        selected = frame.loc[frame["requires_manual_verification"]].copy()
        if not include_completed:
            selected = selected.loc[selected["manual_decision"].isna()].copy()
        selected["_desktop_probability_order"] = pd.to_numeric(
            selected["probablity"], errors="coerce"
        )
        return (
            selected.sort_values(
                ["_desktop_probability_order", "manual_review_rank", "pair_key"],
                ascending=[True, True, True],
                na_position="last",
                kind="mergesort",
            )
            .drop(columns=["_desktop_probability_order"])
            .reset_index(drop=True)
        )

    def review_summary(self) -> dict[str, int | float]:
        selected = self.review_rows(include_completed=True)
        completed = int(selected["manual_decision"].notna().sum())
        diagnostics = self.manual_review_diagnostics()
        return {
            "total_candidates": int(diagnostics["total_candidates"]),
            "selected": int(diagnostics["selected"]),
            "completed": completed,
            "remaining": int(len(selected) - completed),
            "threshold": self.optimized_threshold,
            "minimum_review_count": int(diagnostics["minimum"]),
            "maximum_review_count": int(diagnostics["maximum"]),
            "selected_fraction": float(diagnostics["selected_fraction"]),
        }

    def pair_features(self, county_1_id: Any, county_2_id: Any) -> pd.Series:
        normalize = self.namespace["_normalize_id"]
        id_1 = normalize(county_1_id)
        id_2 = normalize(county_2_id)
        frame = self.features_df
        rows = frame.loc[
            frame["county_1_road_id"].map(normalize).eq(id_1)
            & frame["county_2_road_id"].map(normalize).eq(id_2)
        ].copy()
        if rows.empty:
            raise KeyError(f"Pair not found: {id_1}/{id_2}")
        rows["_probability_sort"] = pd.to_numeric(
            rows["probablity"], errors="coerce"
        )
        return rows.sort_values(
            "_probability_sort",
            ascending=False,
            na_position="last",
            kind="mergesort",
        ).iloc[0]

    def record_manual_decision(self, pair_key: str, decision: str) -> None:
        """Record a decision in RAM only; no local file is touched here."""
        self._require_analysis()
        normalized = decision.strip().lower()
        if normalized not in {"yes", "no"}:
            raise ValueError("Manual decision must be 'yes' or 'no'.")
        frame = self.decision_df
        matches = frame.index[
            frame["pair_key"].astype(str).eq(str(pair_key))
        ].tolist()
        if len(matches) != 1:
            raise KeyError(
                f"Expected one decision row for pair key {pair_key!r}; found {len(matches)}."
            )
        row_index = matches[0]
        if not bool(frame.at[row_index, "requires_manual_verification"]):
            raise ValueError("The selected pair is not part of the manual-review queue.")
        if frame["manual_decision"].dtype != object:
            frame["manual_decision"] = frame["manual_decision"].astype("object")
        frame.at[row_index, "manual_decision"] = normalized
        self.namespace["section_24_decision_df"] = frame
        self._session_dirty = True
        self.log(
            f"Recorded manual decision {normalized.upper()} for {pair_key} in RAM. "
            "It will be written when review/app closes."
        )

    def context_layers(self) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        self._require_analysis()
        first = self.namespace.get("marion_match", self.namespace["marion_gdf"])
        second = self.namespace.get("hamilton_match", self.namespace["hamilton_gdf"])
        return first, second

    def finalize(self) -> FinalizationResult:
        self._require_analysis()
        pending = self.review_rows(include_completed=False)
        if not pending.empty:
            raise ValueError(f"{len(pending)} manual-review pairs remain unanswered.")

        if self._decision_audit_output is None:
            raise RuntimeError("Decision-audit output path was not initialized.")
        self.namespace["DECISION_AUDIT_CSV"] = self._decision_audit_output

        output_1, output_2 = self.namespace["finalize_decisions_and_create_outputs"](
            auto_download=False
        )
        decisions = self.namespace["section_24_decision_df"]
        accepted = int(decisions["final_decision"].eq("yes").sum())
        rejected = int(decisions["final_decision"].eq("no").sum())
        return FinalizationResult(
            county_1_output=Path(output_1),
            county_2_output=Path(output_2),
            final_decisions_csv=Path(self.namespace["SECTION_24_OUTPUT_CSV"]),
            decision_audit_csv=Path(self.namespace["DECISION_AUDIT_CSV"]),
            connection_audit_csv=Path(self.namespace["CONNECTION_AUDIT_CSV"]),
            accepted_pairs=accepted,
            rejected_pairs=rejected,
        )
