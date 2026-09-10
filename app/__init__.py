"""Road Matcher Desktop application."""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path


_PROJECT_NAME = "road-matcher-desktop"
_VERSION_PATTERN = re.compile(
    r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$'
)


def _version_from_pyproject(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _VERSION_PATTERN.search(text)
    if match is None:
        return None

    value = match.group(1).strip()
    return value or None


def _load_version() -> str:
    """Return the project version without maintaining a second version literal."""

    candidate_roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidate_roots.append(Path(bundle_root))

    candidate_roots.append(Path(__file__).resolve().parents[1])

    for candidate_root in candidate_roots:
        value = _version_from_pyproject(candidate_root / "pyproject.toml")
        if value:
            return value

    # This fallback covers a normal installed-package layout where the source
    # pyproject file is not present. Distribution metadata is generated from
    # pyproject.toml when the package is built, so it retains the same source
    # of truth.
    try:
        return distribution_version(_PROJECT_NAME)
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "Could not determine the Road Matcher version from pyproject.toml "
            "or installed package metadata."
        ) from exc


__version__ = _load_version()
