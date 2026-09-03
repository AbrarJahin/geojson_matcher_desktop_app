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
from typing import Any, Callable, Iterable, Optional

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
from app.core.junctions import (
    JunctionProposal,
    build_junction_proposals,
    junction_signature,
    road_key,
    update_member_geometry,
)

LOGGER = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
StageCallback = Callable[[int, int, str], None]

VALID_EXTENSIONS = {".json", ".geojson"}
SESSION_SCHEMA_VERSION = 3
JUNCTION_STATE_SCHEMA_VERSION = 1

# Section 18.0 single Safe Reject threshold: reject only below the
# lowest probability observed among labeled VALID pairs (strict < rule).
SAFE_REJECT_GLOBAL_THRESHOLD = 0.937979492358832


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
class JunctionRoundResult:
    completed_round: int
    accepted_junctions: int
    rejected_junctions: int
    geometry_changed: bool
    next_round: int
    next_junctions: int
    deferred_junctions: int

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
        self._pair_run_key = (
            f"{safe_run_key_part(self.config.county_file_1.stem)}"
            f"__{safe_run_key_part(self.config.county_file_2.stem)}"
        )
        self._state_dir = self.config.output_dir / ".road_matcher_state"
        self._session_csv_path = (
            self._state_dir / f"{self._pair_run_key}_manual_review_progress.csv"
        )
        self._junction_state_path = (
            self._state_dir / f"{self._pair_run_key}_junction_state.json"
        )
        self._working_file_1 = (
            self._state_dir / f"{self._pair_run_key}_working_1.geojson"
        )
        self._working_file_2 = (
            self._state_dir / f"{self._pair_run_key}_working_2.geojson"
        )
        self._loaded_session_path: Path | None = None
        self._decision_audit_output: Path | None = None
        self._session_recovery_warning: str | None = None
        self._restart_from_original_inputs = False

        # Junction orchestration is layered on top of the unchanged pair-level
        # analytical pipeline. A road can appear in only one proposal within a
        # round; deferred conflicts are reconsidered after the round completes.
        self._junction_round = 1
        self._junctions: list[JunctionProposal] = []
        self._junction_drafts: dict[str, dict[str, Any]] = {}
        self._rejected_junction_signatures: set[str] = set()
        self._resolved_junction_signatures: set[str] = set()
        self._accepted_pair_keys: set[str] = set()
        self._accepted_junction_history: list[dict[str, Any]] = []
        self._deferred_junction_count = 0
        self._junction_state_loaded = False

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

    def _analysis_input_files(self) -> tuple[Path, Path]:
        if self._accepted_junction_history:
            if not self._working_file_1.is_file() or not self._working_file_2.is_file():
                raise RuntimeError(
                    "The saved junction state references updated working GeoJSON files "
                    "that are missing. Restore the .road_matcher_state folder or remove "
                    "the junction state file to restart from the original inputs."
                )
            return self._working_file_1, self._working_file_2
        return self.config.county_file_1, self.config.county_file_2

    def _quarantine_unusable_session(self) -> Path | None:
        """Move an incomplete session aside so it cannot be loaded again."""
        candidates = [
            self._junction_state_path,
            self._working_file_1,
            self._working_file_2,
            *self._session_candidates(),
        ]
        existing = list(dict.fromkeys(path for path in candidates if path.is_file()))
        if not existing:
            return None

        archive_dir = (
            self._state_dir
            / "unusable_sessions"
            / (
                f"{self._pair_run_key}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"
            )
        )
        try:
            archive_dir.mkdir(parents=True, exist_ok=False)
        except OSError:
            LOGGER.exception("Could not create an archive for the incomplete session.")
            return None

        moved_any = False
        for source in existing:
            try:
                shutil.move(str(source), str(archive_dir / source.name))
                moved_any = True
            except OSError:
                LOGGER.exception("Could not archive incomplete session file: %s", source)
        return archive_dir if moved_any else None

    def _load_junction_state(self) -> None:
        if self._junction_state_loaded:
            return
        self._junction_state_loaded = True
        if not self._junction_state_path.is_file():
            return
        try:
            state = json.loads(self._junction_state_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("Ignoring unreadable junction state: %s", self._junction_state_path)
            return

        if int(state.get("schema_version", -1)) != JUNCTION_STATE_SCHEMA_VERSION:
            self.log(f"Ignoring incompatible junction state: {self._junction_state_path}")
            return
        if str(state.get("session_signature", "")) != self._session_signature:
            self.log(
                "Ignoring saved junction state because the original inputs, settings, "
                "or analytical algorithm changed."
            )
            return

        accepted_junctions = list(state.get("accepted_junctions", []))
        missing_working_files = [
            path
            for path in (self._working_file_1, self._working_file_2)
            if not path.is_file()
        ]
        if accepted_junctions and missing_working_files:
            archive_dir = self._quarantine_unusable_session()
            missing_names = ", ".join(path.name for path in missing_working_files)
            self._session_recovery_warning = (
                "A compatible junction session was found, but one or both working "
                "GeoJSON files are missing from .road_matcher_state.\n\n"
                f"Missing: {missing_names}\n\n"
                "The incomplete saved session cannot be resumed. It was ignored, "
                "and analysis restarted from the original two GeoJSON files."
            )
            if archive_dir is not None:
                self._session_recovery_warning += (
                    f"\n\nThe unusable session was preserved here:\n{archive_dir}"
                )
            self._restart_from_original_inputs = True
            self.log(self._session_recovery_warning)
            return

        self._junction_round = max(1, int(state.get("junction_round", 1)))
        self._rejected_junction_signatures = set(
            str(value) for value in state.get("rejected_signatures", [])
        )
        self._resolved_junction_signatures = set(
            str(value) for value in state.get("resolved_signatures", [])
        )
        self._accepted_pair_keys = set(
            str(value) for value in state.get("accepted_pair_keys", [])
        )
        self._accepted_junction_history = accepted_junctions
        drafts = state.get("current_round_drafts", {})
        self._junction_drafts = drafts if isinstance(drafts, dict) else {}

        if self._accepted_junction_history:
            self._loaded_session_path = self._junction_state_path
            self.log(
                f"Restored junction session at round {self._junction_round}: "
                f"{self._junction_state_path}"
            )
        elif self._junction_drafts or self._rejected_junction_signatures:
            self._loaded_session_path = self._junction_state_path
            self.log(f"Restored junction review state: {self._junction_state_path}")

    def _capture_current_junction_drafts(self) -> None:
        for proposal in self._junctions:
            is_default_selection = all(member.selected for member in proposal.members)
            is_default_point = (
                abs(float(proposal.junction_x) - float(proposal.suggested_x)) <= 1e-9
                and abs(float(proposal.junction_y) - float(proposal.suggested_y)) <= 1e-9
            )
            if proposal.decision is None and is_default_selection and is_default_point:
                self._junction_drafts.pop(proposal.signature, None)
                continue
            self._junction_drafts[proposal.signature] = {
                "decision": proposal.decision,
                "selected_member_keys": proposal.selected_member_keys,
                "junction_x": float(proposal.junction_x),
                "junction_y": float(proposal.junction_y),
            }

    def _persist_junction_state(self) -> Path:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._capture_current_junction_drafts()
        state = {
            "schema_version": JUNCTION_STATE_SCHEMA_VERSION,
            "session_signature": self._session_signature,
            "junction_round": int(self._junction_round),
            "rejected_signatures": sorted(self._rejected_junction_signatures),
            "resolved_signatures": sorted(self._resolved_junction_signatures),
            "accepted_pair_keys": sorted(self._accepted_pair_keys),
            "accepted_junctions": self._accepted_junction_history,
            "current_round_drafts": self._junction_drafts,
            "working_file_1": str(self._working_file_1),
            "working_file_2": str(self._working_file_2),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        temp = self._junction_state_path.with_name(
            f".{self._junction_state_path.name}.{os.getpid()}.tmp"
        )
        try:
            temp.write_text(
                json.dumps(state, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            with temp.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._junction_state_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return self._junction_state_path

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
            "safe_reject_global_threshold": SAFE_REJECT_GLOBAL_THRESHOLD,
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
        junction_state = self._persist_junction_state()
        self._loaded_session_path = junction_state
        self._session_dirty = False
        completed = int(frame["manual_decision"].notna().sum())
        junction_completed = sum(
            proposal.decision is not None for proposal in self._junctions
        )
        self.log(
            f"Saved review session during {reason}: pair decisions={completed}, "
            f"junction decisions={junction_completed}; {junction_state}"
        )
        # Keep the historic CSV return value/API for older callers/tests.
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
        self._build_session_identity()
        self._load_junction_state()
        pair_progress = (
            None
            if self._restart_from_original_inputs
            else self._find_session_to_load()
        )
        if self._loaded_session_path is None and pair_progress is not None:
            self._loaded_session_path = pair_progress
        progress_input = pair_progress or self._session_csv_path
        analysis_file_1, analysis_file_2 = self._analysis_input_files()
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
            "SAFE_REJECT_GLOBAL_THRESHOLD": SAFE_REJECT_GLOBAL_THRESHOLD,
            "COUNTY_FILE_1": str(analysis_file_1),
            "COUNTY_FILE_2": str(analysis_file_2),
            "COUNTY_1_NAME": county_name_from_filename(cfg.county_file_1),
            "COUNTY_2_NAME": county_name_from_filename(cfg.county_file_2),
            "MARION_FILE": str(analysis_file_1),
            "HAMILTON_FILE": str(analysis_file_2),
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
        self._prepare_junction_round()
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
            "Two-category decision partition verified: "
            f"SAFE_REJECT={diagnostics['safe_rejected']} "
            f"({diagnostics['safe_reject_fraction']:.2%}); "
            f"MANUAL_REVIEW={diagnostics['selected']} "
            f"({diagnostics['selected_fraction']:.2%})."
        )
        junction_summary = self.junction_review_summary()
        self.log(
            "Every candidate not safely rejected remains available as pair-level evidence. "
            f"Round {self._junction_round} generated {junction_summary['selected']} "
            f"non-overlapping junction(s); deferred conflicts={self._deferred_junction_count}."
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
        """Backward-compatible alias for the global Safe Reject threshold."""
        self._require_analysis()
        return float(self.namespace["SAFE17_GLOBAL_THRESHOLD"])

    @property
    def safe_reject_thresholds(self) -> dict[str, float]:
        self._require_analysis()
        return {
            "global": float(self.namespace["SAFE17_GLOBAL_THRESHOLD"]),
        }

    @property
    def has_unsaved_decisions(self) -> bool:
        return bool(self._analysis_complete and self._session_dirty)

    @property
    def session_path(self) -> Path:
        return self._session_csv_path

    @property
    def loaded_session_path(self) -> Path | None:
        return self._loaded_session_path

    @property
    def session_recovery_warning(self) -> str | None:
        return self._session_recovery_warning

    @staticmethod
    def _manual_review_bounds_for_total(
        total_pairs: int, minimum_fraction: float, maximum_fraction: float
    ) -> tuple[int, int]:
        """Compatibility helper retained for callers from older app versions.

        The two-category workflow no longer samples a percentage of candidates.
        Its true review count is determined by the Safe Reject partition.
        """
        total_pairs = int(total_pairs)
        if total_pairs < 0:
            raise ValueError("total_pairs cannot be negative.")
        return 0, total_pairs

    def manual_review_diagnostics(self) -> dict[str, int | float | bool]:
        """Validate that every non-Safe-Reject pair is queued for manual review."""
        frame = self.decision_df
        total = int(len(frame))
        manual_mask = frame["requires_manual_verification"].astype(bool)
        selected = int(manual_mask.sum())

        if "safe_reject_decision" not in frame.columns:
            raise RuntimeError("The two-category Safe Reject decision column is missing.")

        safe_mask = frame["safe_reject_decision"].astype(str).eq("SAFE_REJECT")
        manual_category_mask = (
            frame["safe_reject_decision"].astype(str).eq("MANUAL_REVIEW")
        )
        safe_rejected = int(safe_mask.sum())

        partition_valid = bool(
            (safe_mask ^ manual_category_mask).all()
            and manual_mask.equals(manual_category_mask)
            and selected + safe_rejected == total
        )
        if not partition_valid:
            raise RuntimeError(
                "The two-category partition is invalid: every pair must be exactly one "
                "of SAFE_REJECT or MANUAL_REVIEW, and every MANUAL_REVIEW pair must "
                "be present in the review queue."
            )

        selected_fraction = (selected / total) if total else 0.0
        safe_fraction = (safe_rejected / total) if total else 0.0
        return {
            "total_candidates": total,
            "selected": selected,
            "safe_rejected": safe_rejected,
            "minimum": selected,
            "maximum": selected,
            "selected_fraction": selected_fraction,
            "safe_reject_fraction": safe_fraction,
            "minimum_fraction": selected_fraction,
            "maximum_fraction": selected_fraction,
            "within_bounds": partition_valid,
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
        thresholds = self.safe_reject_thresholds
        return {
            "total_candidates": int(diagnostics["total_candidates"]),
            "safe_rejected": int(diagnostics["safe_rejected"]),
            "safe_reject_fraction": float(diagnostics["safe_reject_fraction"]),
            "selected": int(diagnostics["selected"]),
            "completed": completed,
            "remaining": int(len(selected) - completed),
            "threshold": thresholds["global"],
            "global_threshold": thresholds["global"],
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

    def _prepare_junction_round(self) -> None:
        """Convert pair-level MANUAL_REVIEW evidence into one disjoint junction round."""
        endpoint_tolerance = min(
            float(self.config.buffer_distance_meters),
            float(self.namespace.get("SECTION_14_TIGHT_CONNECTION_TOLERANCE_M", 30.0)),
        )
        excluded = (
            set(self._rejected_junction_signatures)
            | set(self._resolved_junction_signatures)
        )
        junctions, deferred = build_junction_proposals(
            self.decision_df,
            round_number=self._junction_round,
            group_radius_m=float(self.config.buffer_distance_meters),
            endpoint_tolerance_m=endpoint_tolerance,
            excluded_signatures=excluded,
            draft_states=self._junction_drafts,
        )
        self._junctions = junctions
        self._deferred_junction_count = int(deferred)
        active_signatures = {proposal.signature for proposal in junctions}
        self._junction_drafts = {
            signature: draft
            for signature, draft in self._junction_drafts.items()
            if signature in active_signatures
        }

    def junction_review_items(self, include_completed: bool = True) -> list[JunctionProposal]:
        self._require_analysis()
        if include_completed:
            return list(self._junctions)
        return [proposal for proposal in self._junctions if proposal.decision is None]

    def junction_review_summary(self) -> dict[str, int | float]:
        self._require_analysis()
        total = len(self._junctions)
        completed = sum(proposal.decision is not None for proposal in self._junctions)
        manual_pairs = int(
            self.decision_df["requires_manual_verification"].astype(bool).sum()
        )
        return {
            "round": int(self._junction_round),
            "selected": int(total),
            "completed": int(completed),
            "remaining": int(total - completed),
            "deferred": int(self._deferred_junction_count),
            "manual_pairs": manual_pairs,
            "accepted_history": int(len(self._accepted_junction_history)),
            "rejected_history": int(len(self._rejected_junction_signatures)),
        }

    def junction_state_path(self) -> Path:
        return self._junction_state_path

    def junction_member_geometry(self, member: Any) -> Any:
        self._require_analysis()
        normalize = self.namespace["_normalize_id"]
        layer = (
            self.namespace["marion_gdf"]
            if int(member.county_index) == 1
            else self.namespace["hamilton_gdf"]
        )
        road_id = normalize(member.road_id)
        matches = layer.loc[
            layer[self.config.road_id_column].map(normalize).eq(road_id)
        ]
        if len(matches) != 1:
            raise KeyError(
                f"Expected one road for junction member {member.key}; found {len(matches)}."
            )
        return matches.geometry.iloc[0]

    def record_junction_decision(
        self,
        signature: str,
        decision: str,
        *,
        selected_member_keys: Iterable[str] | None = None,
        junction_x: float | None = None,
        junction_y: float | None = None,
    ) -> None:
        self._require_analysis()
        normalized = str(decision).strip().lower()
        if normalized not in {"accept", "reject"}:
            raise ValueError("Junction decision must be 'accept' or 'reject'.")
        proposal = next(
            (item for item in self._junctions if item.signature == str(signature)),
            None,
        )
        if proposal is None:
            raise KeyError(f"Junction proposal not found: {signature}")

        if selected_member_keys is not None:
            selected = {str(value) for value in selected_member_keys}
            known = {member.key for member in proposal.members}
            if not selected.issubset(known):
                raise ValueError("The selected road list contains a road outside this junction.")
            for member in proposal.members:
                member.selected = member.key in selected

        if junction_x is not None:
            proposal.junction_x = float(junction_x)
        if junction_y is not None:
            proposal.junction_y = float(junction_y)

        if normalized == "accept":
            selected_members = proposal.selected_members
            if len(selected_members) < 2:
                raise ValueError("An accepted junction must contain at least two roads.")
            if {member.county_index for member in selected_members} != {1, 2}:
                raise ValueError(
                    "An accepted cross-county junction must keep at least one road "
                    "from each input dataset."
                )
            junction = Point(proposal.junction_x, proposal.junction_y)
            excessive = [
                (member.road_name, float(member.contact_point.distance(junction)))
                for member in selected_members
                if float(member.contact_point.distance(junction))
                > float(self.config.buffer_distance_meters) + 1e-7
            ]
            if excessive:
                road_name, movement = max(excessive, key=lambda item: item[1])
                raise ValueError(
                    f"The proposed junction would move {road_name} by {movement:.2f} m, "
                    f"beyond the configured {self.config.buffer_distance_meters:.2f} m "
                    "candidate distance. Move the junction point closer or uncheck that road."
                )

        proposal.decision = normalized
        self._junction_drafts[proposal.signature] = {
            "decision": proposal.decision,
            "selected_member_keys": proposal.selected_member_keys,
            "junction_x": float(proposal.junction_x),
            "junction_y": float(proposal.junction_y),
        }
        self._session_dirty = True
        self.log(
            f"Recorded {normalized.upper()} for {proposal.junction_id} in RAM "
            f"({len(proposal.selected_members)} selected road(s))."
        )

    @staticmethod
    def _raw_geojson_feature_index(
        geojson_object: dict[str, Any],
        road_id_column: str,
        normalize: Callable[[Any], str],
    ) -> dict[str, int]:
        index: dict[str, int] = {}
        for position, feature in enumerate(geojson_object.get("features", [])):
            properties = feature.get("properties", {}) or {}
            identifier = properties.get(road_id_column, feature.get("id"))
            index[normalize(identifier)] = position
        return index

    @staticmethod
    def _write_json_temp(payload: dict[str, Any], target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return temp

    def _apply_accepted_junctions(
        self, accepted: list[JunctionProposal]
    ) -> None:
        if not accepted:
            return

        source_file_1, source_file_2 = self._analysis_input_files()
        normalize = self.namespace["_normalize_id"]
        county_1 = self.namespace["marion_gdf"].copy()
        county_2 = self.namespace["hamilton_gdf"].copy()
        index_1 = {
            normalize(value): index
            for index, value in county_1[self.config.road_id_column].items()
        }
        index_2 = {
            normalize(value): index
            for index, value in county_2[self.config.road_id_column].items()
        }

        changed_1: set[str] = set()
        changed_2: set[str] = set()
        new_history: list[dict[str, Any]] = []

        for proposal in accepted:
            junction = Point(proposal.junction_x, proposal.junction_y)
            member_audit: list[dict[str, Any]] = []
            selected_keys = {member.key for member in proposal.selected_members}
            selected_pair_keys: list[str] = []
            for pair_key in proposal.pair_keys:
                try:
                    id_1, id_2 = str(pair_key).split("||", 1)
                except ValueError:
                    continue
                if road_key(1, id_1) in selected_keys and road_key(2, id_2) in selected_keys:
                    selected_pair_keys.append(str(pair_key))
                    self._accepted_pair_keys.add(str(pair_key))

            for member in proposal.selected_members:
                layer = county_1 if member.county_index == 1 else county_2
                index_map = index_1 if member.county_index == 1 else index_2
                normalized_id = normalize(member.road_id)
                if normalized_id not in index_map:
                    raise ValueError(f"Road ID not found while applying {proposal.junction_id}: {member.road_id}")
                row_index = index_map[normalized_id]
                geometry = layer.at[row_index, "geometry"]
                new_geometry, movement_m = update_member_geometry(
                    geometry,
                    member,
                    junction,
                )
                if movement_m > float(self.config.buffer_distance_meters) + 1e-7:
                    raise ValueError(
                        f"{proposal.junction_id} would move road {member.road_id} "
                        f"{movement_m:.2f} m, beyond the configured candidate distance."
                    )
                layer.at[row_index, "geometry"] = new_geometry
                if member.county_index == 1:
                    changed_1.add(normalized_id)
                else:
                    changed_2.add(normalized_id)
                member_audit.append(
                    {
                        "key": member.key,
                        "county_index": int(member.county_index),
                        "road_id": member.road_id,
                        "road_name": member.road_name,
                        "attachment_type": member.attachment_type,
                        "part_index": int(member.part_index),
                        "measure_m": float(member.measure_m),
                        "original_contact_x": float(member.contact_x),
                        "original_contact_y": float(member.contact_y),
                        "movement_m": float(movement_m),
                    }
                )

            selected_signature = junction_signature(proposal.selected_members)
            self._resolved_junction_signatures.add(proposal.signature)
            self._resolved_junction_signatures.add(selected_signature)
            history = proposal.to_state_dict()
            history["selected_signature"] = selected_signature
            history["selected_pair_keys"] = selected_pair_keys
            history["member_audit"] = member_audit
            history["accepted_at"] = datetime.now().isoformat(timespec="seconds")
            new_history.append(history)

        with source_file_1.open("r", encoding="utf-8") as stream:
            raw_1 = json.load(stream)
        with source_file_2.open("r", encoding="utf-8") as stream:
            raw_2 = json.load(stream)
        raw_index_1 = self._raw_geojson_feature_index(
            raw_1, self.config.road_id_column, normalize
        )
        raw_index_2 = self._raw_geojson_feature_index(
            raw_2, self.config.road_id_column, normalize
        )

        for road_id in changed_1:
            if road_id not in raw_index_1:
                raise ValueError(f"Raw {self.county_1_name} road ID not found: {road_id}")
            projected = county_1.at[index_1[road_id], "geometry"]
            latlon = gpd.GeoSeries([projected], crs=self.config.target_crs).to_crs(
                self.config.latlon_crs
            ).iloc[0]
            raw_1["features"][raw_index_1[road_id]]["geometry"] = mapping(latlon)

        for road_id in changed_2:
            if road_id not in raw_index_2:
                raise ValueError(f"Raw {self.county_2_name} road ID not found: {road_id}")
            projected = county_2.at[index_2[road_id], "geometry"]
            latlon = gpd.GeoSeries([projected], crs=self.config.target_crs).to_crs(
                self.config.latlon_crs
            ).iloc[0]
            raw_2["features"][raw_index_2[road_id]]["geometry"] = mapping(latlon)

        temp_1 = self._write_json_temp(raw_1, self._working_file_1)
        temp_2 = self._write_json_temp(raw_2, self._working_file_2)
        try:
            os.replace(temp_1, self._working_file_1)
            os.replace(temp_2, self._working_file_2)
        finally:
            temp_1.unlink(missing_ok=True)
            temp_2.unlink(missing_ok=True)

        self._accepted_junction_history.extend(new_history)
        self.log(
            f"Applied {len(accepted)} accepted junction(s) to the working GeoJSON files."
        )

    def complete_junction_round(
        self, stage_callback: StageCallback | None = None
    ) -> JunctionRoundResult:
        self._require_analysis()
        pending = [proposal for proposal in self._junctions if proposal.decision is None]
        if pending:
            raise ValueError(f"{len(pending)} junction(s) remain unanswered in this round.")

        completed_round = int(self._junction_round)
        accepted = [proposal for proposal in self._junctions if proposal.decision == "accept"]
        rejected = [proposal for proposal in self._junctions if proposal.decision == "reject"]

        for proposal in rejected:
            self._rejected_junction_signatures.add(proposal.signature)

        geometry_changed = bool(accepted)
        if accepted:
            self._apply_accepted_junctions(accepted)

        self._junction_round += 1
        self._junctions = []
        self._junction_drafts = {}
        self._session_dirty = True

        if geometry_changed:
            # Persist the updated working geometries and accepted/rejected memory
            # before rerunning the unchanged analytical pipeline from the beginning.
            self._persist_junction_state()
            self.log(
                f"Round {completed_round} changed geometry; rerunning the complete "
                "pair generation + ML + fusion + Safe Reject pipeline."
            )
            self.run_analysis(stage_callback=stage_callback)
        else:
            # Geometry/model inputs are unchanged. Re-plan from the same ML evidence
            # using the enlarged rejection list so previously deferred lower-score
            # junctions can surface without an unnecessary identical ML rerun.
            self._prepare_junction_round()
            self._persist_junction_state()
            self._session_dirty = False
            self.log(
                f"Round {completed_round} changed no geometry; regenerated junction "
                "proposals from the existing model results and rejection memory."
            )

        summary = self.junction_review_summary()
        return JunctionRoundResult(
            completed_round=completed_round,
            accepted_junctions=len(accepted),
            rejected_junctions=len(rejected),
            geometry_changed=geometry_changed,
            next_round=int(summary["round"]),
            next_junctions=int(summary["selected"]),
            deferred_junctions=int(summary["deferred"]),
        )

    def _finalize_junction_outputs(self) -> FinalizationResult:
        source_file_1, source_file_2 = self._analysis_input_files()
        output_1 = self.config.output_dir / f"{self.config.county_file_1.stem}_updated.json"
        output_2 = self.config.output_dir / f"{self.config.county_file_2.stem}_updated.json"
        shutil.copy2(source_file_1, output_1)
        shutil.copy2(source_file_2, output_2)

        decision_output = Path(self.namespace["SECTION_24_OUTPUT_CSV"])
        audit_output = self._decision_audit_output or (
            self.config.output_dir / f"{self._pair_run_key}_decision_audit.csv"
        )
        connection_output = Path(self.namespace["CONNECTION_AUDIT_CSV"])

        decisions = self.decision_df.copy()
        decisions["final_decision"] = np.where(
            decisions["pair_key"].astype(str).isin(self._accepted_pair_keys),
            "yes",
            "no",
        )
        decisions[["county_1_id", "county_2_id", "final_decision"]].rename(
            columns={"final_decision": "is_valid"}
        ).to_csv(decision_output, index=False)
        decisions.to_csv(audit_output, index=False)

        connection_rows: list[dict[str, Any]] = []
        transformer = Transformer.from_crs(
            self.config.target_crs,
            self.config.latlon_crs,
            always_xy=True,
        )
        for history in self._accepted_junction_history:
            longitude, latitude = transformer.transform(
                float(history["junction_x"]),
                float(history["junction_y"]),
            )
            for member in history.get("member_audit", []):
                connection_rows.append(
                    {
                        "junction_id": history.get("junction_id"),
                        "round_number": history.get("round_number"),
                        "junction_signature": history.get("signature"),
                        "average_probability": history.get("average_probability"),
                        "county_index": member.get("county_index"),
                        "road_id": member.get("road_id"),
                        "road_name": member.get("road_name"),
                        "attachment_type": member.get("attachment_type"),
                        "part_index": member.get("part_index"),
                        "movement_m": member.get("movement_m"),
                        "shared_junction_longitude": longitude,
                        "shared_junction_latitude": latitude,
                    }
                )
        pd.DataFrame(connection_rows).to_csv(connection_output, index=False)
        self._persist_junction_state()

        accepted_pairs = int(decisions["final_decision"].eq("yes").sum())
        rejected_pairs = int(decisions["final_decision"].eq("no").sum())
        return FinalizationResult(
            county_1_output=output_1,
            county_2_output=output_2,
            final_decisions_csv=decision_output,
            decision_audit_csv=Path(audit_output),
            connection_audit_csv=connection_output,
            accepted_pairs=accepted_pairs,
            rejected_pairs=rejected_pairs,
        )

    def context_layers(self) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        self._require_analysis()
        first = self.namespace.get("marion_match", self.namespace["marion_gdf"])
        second = self.namespace.get("hamilton_match", self.namespace["hamilton_gdf"])
        return first, second

    def finalize(self) -> FinalizationResult:
        self._require_analysis()

        # Backward compatibility: callers/tests that still complete the old pair
        # queue explicitly continue to use the untouched notebook finalizer.
        manual_rows = self.review_rows(include_completed=True)
        legacy_pair_review_complete = bool(
            not manual_rows.empty
            and manual_rows["manual_decision"].notna().all()
            and all(proposal.decision is None for proposal in self._junctions)
            and not self._accepted_junction_history
            and not self._rejected_junction_signatures
        )
        if legacy_pair_review_complete:
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

        pending_junctions = [
            proposal for proposal in self._junctions if proposal.decision is None
        ]
        if pending_junctions:
            raise ValueError(
                f"{len(pending_junctions)} junction(s) remain unanswered."
            )
        if self._junctions:
            raise ValueError(
                "The current junction round is reviewed but has not been committed. "
                "Complete the round before creating final outputs."
            )
        return self._finalize_junction_outputs()
