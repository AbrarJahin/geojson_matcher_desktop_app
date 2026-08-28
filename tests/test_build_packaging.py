from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "road_matcher_project_tasks", ROOT / "scripts" / "project_tasks.py"
)
assert _SPEC is not None and _SPEC.loader is not None
project_tasks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(project_tasks)


def test_verify_frozen_application_runs_packaging_smoke_test(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "RoadMatcher.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setattr(project_tasks, "DIST_EXE", executable)
    monkeypatch.setattr(
        project_tasks, "PACKAGING_SMOKE_REPORT", tmp_path / "packaging-smoke.log"
    )
    observed: dict[str, object] = {}

    def fake_run(command, *, cwd, env, check, timeout):  # type: ignore[no-untyped-def]
        observed["command"] = command
        observed["cwd"] = cwd
        observed["env"] = env
        observed["check"] = check
        observed["timeout"] = timeout
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(project_tasks.subprocess, "run", fake_run)

    project_tasks.verify_frozen_application()

    assert observed["command"] == [
        str(executable),
        project_tasks.PACKAGING_SMOKE_TEST_ARGUMENT,
    ]
    assert observed["cwd"] == str(project_tasks.ROOT)
    assert observed["env"]["ROAD_MATCHER_PACKAGING_SMOKE_REPORT"] == str(
        project_tasks.PACKAGING_SMOKE_REPORT
    )
    assert observed["check"] is False
    assert observed["timeout"] == 120


def test_verify_frozen_application_rejects_broken_gis_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "RoadMatcher.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setattr(project_tasks, "DIST_EXE", executable)
    monkeypatch.setattr(
        project_tasks, "PACKAGING_SMOKE_REPORT", tmp_path / "packaging-smoke.log"
    )
    monkeypatch.setattr(
        project_tasks.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7),
    )

    with pytest.raises(RuntimeError, match="GIS smoke test failed"):
        project_tasks.verify_frozen_application()
