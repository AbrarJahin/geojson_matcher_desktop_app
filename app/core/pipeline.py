from __future__ import annotations

import builtins
import json
import math
import os
import re
import shutil
import time
import warnings
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

from app.core.legacy_program import execute_legacy_pipeline

LogCallback = Callable[[str], None]
StageCallback = Callable[[int, int, str], None]

VALID_EXTENSIONS = {".json", ".geojson"}


def county_name_from_filename(path_or_name: str | Path) -> str:
    stem = Path(path_or_name).stem
    stem = re.sub(r"(_County)?_Boundary.*$", "", stem, flags=re.IGNORECASE)
    stem = stem.strip("_ ").strip()
    if not stem:
        raise ValueError(f"Could not derive a readable name from {path_or_name!r}.")
    return stem


def safe_run_key_part(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")


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
            [self.county_file_1.expanduser().resolve(), self.county_file_2.expanduser().resolve()],
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
            raise ValueError("Manual-review fractions must satisfy 0 < minimum <= maximum <= 1.")
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
    """Desktop service wrapper around the notebook's retained model pipeline."""

    def __init__(self, config: PipelineConfig, logger: LogCallback | None = None):
        self.config = config.normalized()
        self.config.validate()
        self._logger = logger or (lambda message: None)
        self.namespace: dict[str, Any] = {}
        self._analysis_complete = False

    def log(self, message: Any) -> None:
        text = str(message).rstrip()
        if text:
            self._logger(text)

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
    def _latest_progress_path(state_dir: Path, pair_run_key: str) -> Path:
        """Return the newest usable progress file for this input pair."""
        primary = state_dir / f"{pair_run_key}_manual_review_progress.csv"
        candidates = [primary]
        candidates.extend(
            state_dir.glob(f"{pair_run_key}_manual_review_progress_recovery_*.csv")
        )
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            return primary
        return max(existing, key=lambda path: path.stat().st_mtime_ns)

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
        manual_rows["source_file_1"] = str(self.config.county_file_1)
        manual_rows["source_file_2"] = str(self.config.county_file_2)
        manual_rows["saved_at"] = datetime.now().isoformat(timespec="seconds")
        return manual_rows

    def _save_manual_progress_safely(self) -> Path:
        """Persist review progress without allowing a Windows file lock to stop review."""
        target = Path(self.namespace["MANUAL_PROGRESS_CSV"])
        target.parent.mkdir(parents=True, exist_ok=True)
        manual_rows = self._manual_progress_frame()
        temp_path = target.with_name(
            f".{target.name}.{os.getpid()}.{datetime.now().strftime('%H%M%S%f')}.tmp"
        )
        manual_rows.to_csv(temp_path, index=False)

        last_error: OSError | None = None
        for delay in (0.0, 0.05, 0.15, 0.30, 0.60):
            if delay:
                time.sleep(delay)
            try:
                os.replace(temp_path, target)
                return target
            except (PermissionError, OSError) as exc:
                last_error = exc

        # Excel, OneDrive, antivirus, or another process can temporarily lock
        # the primary CSV on Windows. Save to a recovery CSV and continue.
        recovery_path = target.with_name(
            f"{target.stem}_recovery_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.csv"
        )
        try:
            os.replace(temp_path, recovery_path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PermissionError(
                "The manual-review progress could not be saved. Close any open "
                "progress CSV in Excel, confirm the folder is writable, and try again. "
                f"Target: {target}"
            ) from last_error

        self.namespace["MANUAL_PROGRESS_CSV"] = recovery_path
        self.log(
            "The normal progress CSV was locked, so progress was saved safely to: "
            f"{recovery_path}"
        )
        return recovery_path

    def _build_namespace(self) -> dict[str, Any]:
        cfg = self.config
        output_dir = cfg.output_dir
        state_dir = output_dir / ".road_matcher_state"
        self._assert_directory_writable(output_dir, "Output folder")
        self._assert_directory_writable(state_dir, "Manual-review state folder")

        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pair_run_key = (
            f"{safe_run_key_part(cfg.county_file_1.stem)}"
            f"__{safe_run_key_part(cfg.county_file_2.stem)}"
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
            "STATE_DIR": state_dir,
            "RUN_TIMESTAMP": run_timestamp,
            "PAIR_RUN_KEY": pair_run_key,
            "MANUAL_PROGRESS_CSV": self._latest_progress_path(state_dir, pair_run_key),
            "SECTION_24_OUTPUT_CSV": output_dir / f"{pair_run_key}_final_pair_decisions.csv",
            "DECISION_AUDIT_CSV": output_dir / f"{pair_run_key}_decision_audit_{run_timestamp}.csv",
            "CONNECTION_AUDIT_CSV": output_dir / f"{pair_run_key}_connection_audit_{run_timestamp}.csv",
        }
        return namespace

    def run_analysis(self, stage_callback: StageCallback | None = None) -> "RoadMatchingPipeline":
        self.namespace = self._build_namespace()
        self.log(f"Input 1: {self.config.county_file_1}")
        self.log(f"Input 2: {self.config.county_file_2}")
        self.log(f"Output directory: {self.config.output_dir}")
        self.log("Running retained notebook analysis pipeline...")
        execute_legacy_pipeline(self.namespace, stage_callback=stage_callback)
        decision_frame = self.namespace["section_24_decision_df"]
        # Explicit object dtype prevents strict pandas builds from rejecting
        # the string values "yes" and "no" when the column began as all NA.
        decision_frame["manual_decision"] = decision_frame["manual_decision"].astype("object")
        self.namespace["section_24_decision_df"] = decision_frame
        self.namespace["_save_manual_progress"] = self._save_manual_progress_safely
        self._analysis_complete = True
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
        return self.namespace.get("COUNTY_1_NAME", county_name_from_filename(self.config.county_file_1))

    @property
    def county_2_name(self) -> str:
        return self.namespace.get("COUNTY_2_NAME", county_name_from_filename(self.config.county_file_2))

    @property
    def optimized_threshold(self) -> float:
        self._require_analysis()
        return float(self.namespace["OPTIMISED_THRESHOLD"])

    def review_rows(self, include_completed: bool = True) -> pd.DataFrame:
        frame = self.decision_df
        selected = frame.loc[frame["requires_manual_verification"]].copy()
        if not include_completed:
            selected = selected.loc[selected["manual_decision"].isna()].copy()
        return selected.sort_values(
            ["manual_pair_number", "pair_key"], kind="mergesort"
        ).reset_index(drop=True)

    def review_summary(self) -> dict[str, int | float]:
        selected = self.review_rows(include_completed=True)
        completed = int(selected["manual_decision"].notna().sum())
        total_candidates = int(len(self.decision_df))
        return {
            "total_candidates": total_candidates,
            "selected": int(len(selected)),
            "completed": completed,
            "remaining": int(len(selected) - completed),
            "threshold": self.optimized_threshold,
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
        rows["_probability_sort"] = pd.to_numeric(rows["probablity"], errors="coerce")
        return rows.sort_values(
            "_probability_sort", ascending=False, na_position="last", kind="mergesort"
        ).iloc[0]

    def record_manual_decision(self, pair_key: str, decision: str) -> None:
        self._require_analysis()
        normalized = decision.strip().lower()
        if normalized not in {"yes", "no"}:
            raise ValueError("Manual decision must be 'yes' or 'no'.")
        frame = self.decision_df
        matches = frame.index[frame["pair_key"].astype(str).eq(str(pair_key))].tolist()
        if len(matches) != 1:
            raise KeyError(f"Expected one decision row for pair key {pair_key!r}; found {len(matches)}.")
        row_index = matches[0]
        if not bool(frame.at[row_index, "requires_manual_verification"]):
            raise ValueError("The selected pair is not part of the manual-review queue.")
        if frame["manual_decision"].dtype != object:
            frame["manual_decision"] = frame["manual_decision"].astype("object")
        previous_value = frame.at[row_index, "manual_decision"]
        frame.at[row_index, "manual_decision"] = normalized
        self.namespace["section_24_decision_df"] = frame
        try:
            saved_path = self._save_manual_progress_safely()
        except Exception:
            frame.at[row_index, "manual_decision"] = previous_value
            self.namespace["section_24_decision_df"] = frame
            raise
        self.log(
            f"Saved manual decision {normalized.upper()} for {pair_key}. "
            f"Progress file: {saved_path}"
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
