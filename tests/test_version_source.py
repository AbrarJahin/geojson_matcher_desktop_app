from __future__ import annotations

import re
from pathlib import Path

import app
import scripts.project_tasks as project_tasks


ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
        text,
    )
    assert match is not None
    return match.group(1)


def test_runtime_version_matches_pyproject() -> None:
    assert app.__version__ == _pyproject_version()


def test_build_version_matches_pyproject() -> None:
    version = _pyproject_version()
    assert project_tasks.PROJECT_VERSION == version
    assert project_tasks.INSTALLER_OUTPUT.name == (
        f"RoadMatcher-Setup-{version}.exe"
    )


def test_runtime_version_parser_rejects_missing_file(tmp_path: Path) -> None:
    assert app._version_from_pyproject(tmp_path / "missing.toml") is None


def test_runtime_version_parser_reads_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "example"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    assert app._version_from_pyproject(pyproject) == "9.8.7"
