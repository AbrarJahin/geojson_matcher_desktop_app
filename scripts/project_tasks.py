"""Conda-prefix-backed Make tasks for Road Matcher Desktop.

The Conda installation's base Python may be older than the application Python.
This bootstrap module therefore remains Python 3.9 compatible. ``make setup``
creates a dedicated Conda environment at ``<project>/.venv`` with Python 3.12.
All runtime, test, build, and installer operations execute inside that local
prefix through ``conda run --prefix``; no activation and no ``py`` launcher are
required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

CommandPart = Union[str, os.PathLike]

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_FILE = ROOT / "pyproject.toml"
SPEC_FILE = ROOT / "road_matcher.spec"
INNO_SCRIPT = ROOT / "installer" / "RoadMatcher.iss"
DIST_EXE = ROOT / "dist" / (
    "RoadMatcher.exe" if os.name == "nt" else "RoadMatcher"
)
PACKAGING_SMOKE_TEST_ARGUMENT = "--packaging-smoke-test"
PACKAGING_SMOKE_REPORT = ROOT / "build" / "packaging-smoke-test.log"

CONDA_PYTHON_VERSION = os.environ.get(
    "ROAD_MATCHER_PYTHON_VERSION", "3.12"
).strip()


def project_version() -> str:
    """Read the application version from pyproject.toml.

    Keep this bootstrap helper Python 3.9 compatible; tomllib is unavailable
    there, and the project version is deliberately a simple X.Y.Z value.
    """

    try:
        text = PYPROJECT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "Could not read project version metadata: {0}".format(PYPROJECT_FILE)
        ) from exc

    match = re.search(
        r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
        text,
    )
    if match is None:
        raise RuntimeError(
            "pyproject.toml does not contain a project version."
        )

    value = match.group(1).strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", value) is None:
        raise RuntimeError(
            "Project version must use X.Y.Z format. Received: {0}".format(value)
        )
    return value


PROJECT_VERSION = project_version()
INSTALLER_OUTPUT = (
    ROOT / "installer_output" / "RoadMatcher-Setup-{0}.exe".format(PROJECT_VERSION)
)


def _configured_prefix() -> Path:
    raw = os.environ.get("ROAD_MATCHER_CONDA_PREFIX", str(ROOT / ".venv"))
    cleaned = raw.strip().strip('"')
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


CONDA_PREFIX = _configured_prefix()
CONDA_HISTORY = CONDA_PREFIX / "conda-meta" / "history"


def _display_command(command: Sequence[CommandPart]) -> str:
    return " ".join(
        '"{0}"'.format(part) if " " in str(part) else str(part)
        for part in command
    )


def run_command(
    command: Sequence[CommandPart],
    cwd: Path = ROOT,
    env: Optional[Dict[str, str]] = None,
) -> None:
    print("\n> {0}".format(_display_command(command)), flush=True)
    subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        env=env,
        check=True,
    )


def capture_command(
    command: Sequence[CommandPart],
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def conda_executable() -> str:
    """Return the Conda executable selected by Make or the current process."""
    candidates = [
        os.environ.get("ROAD_MATCHER_CONDA_EXE"),
        os.environ.get("CONDA_EXE"),
        "conda",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = candidate.strip().strip('"')
        candidate_path = Path(cleaned).expanduser()
        if candidate_path.is_file():
            return str(candidate_path.resolve())
        resolved = shutil.which(cleaned)
        if resolved:
            return resolved
    raise RuntimeError(
        "Conda was not found. Run Make with the Conda executable path, for "
        "example: make setup "
        'CONDA="C:/tools/miniconda3/Scripts/conda.exe"'
    )


def requested_python_tuple() -> Tuple[int, int]:
    pieces = CONDA_PYTHON_VERSION.split(".")
    if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
        raise RuntimeError(
            "PYTHON_VERSION must use major.minor form, for example 3.12. "
            "Received: {0}".format(CONDA_PYTHON_VERSION)
        )
    version = (int(pieces[0]), int(pieces[1]))
    if version < (3, 10) or version >= (3, 15):
        raise RuntimeError(
            "Road Matcher supports Python 3.10 through 3.14. Requested: {0}"
            .format(CONDA_PYTHON_VERSION)
        )
    return version


def _normalized_path(path: Union[str, Path]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def conda_run_command(*parts: CommandPart) -> List[str]:
    return [
        conda_executable(),
        "run",
        "--no-capture-output",
        "--prefix",
        str(CONDA_PREFIX),
    ] + [str(part) for part in parts]


def environment_python_info() -> Optional[Dict[str, object]]:
    """Return local Conda Python information, or None when it is absent."""
    if not CONDA_HISTORY.is_file():
        return None

    script = (
        "import json, os, sys; "
        "print(json.dumps({"
        "'version': list(sys.version_info[:3]), "
        "'executable': sys.executable, "
        "'prefix': sys.prefix, "
        "'conda_prefix': os.environ.get('CONDA_PREFIX')"
        "}))"
    )
    result = capture_command(conda_run_command("python", "-c", script))
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def require_environment() -> Dict[str, object]:
    info = environment_python_info()
    if info is None:
        raise RuntimeError(
            "Project-local Conda environment not found at {0}. "
            "Run 'make setup' first.".format(CONDA_PREFIX)
        )

    version_data = info.get("version")
    if not isinstance(version_data, list) or len(version_data) < 2:
        raise RuntimeError("Could not determine the local environment version.")

    requested = requested_python_tuple()
    actual = (int(version_data[0]), int(version_data[1]))
    if actual != requested:
        raise RuntimeError(
            "The environment at {0} uses Python {1}.{2}, but Python {3} is "
            "configured. Run 'make setup' to update it."
            .format(
                CONDA_PREFIX,
                actual[0],
                actual[1],
                CONDA_PYTHON_VERSION,
            )
        )

    actual_prefix = info.get("prefix")
    if not isinstance(actual_prefix, str) or _normalized_path(actual_prefix) != _normalized_path(CONDA_PREFIX):
        raise RuntimeError(
            "Conda returned a different environment prefix. Expected {0}; got {1}."
            .format(CONDA_PREFIX, actual_prefix)
        )
    return info


def _prepare_prefix_for_conda() -> None:
    """Remove only a legacy Python venv; reject unrelated non-Conda content."""
    if not CONDA_PREFIX.exists() or CONDA_HISTORY.is_file():
        return

    try:
        is_empty = not any(CONDA_PREFIX.iterdir())
    except OSError as exc:
        raise RuntimeError(
            "Cannot inspect environment directory {0}: {1}".format(
                CONDA_PREFIX, exc
            )
        )

    if is_empty:
        return

    if (CONDA_PREFIX / "pyvenv.cfg").is_file():
        print(
            "Removing the earlier non-Conda virtual environment at {0}."
            .format(CONDA_PREFIX)
        )
        shutil.rmtree(str(CONDA_PREFIX))
        return

    raise RuntimeError(
        "{0} exists but is not a Conda environment. Move/delete that directory "
        "or run 'make distclean', then run 'make setup' again."
        .format(CONDA_PREFIX)
    )


def _create_or_update_environment() -> None:
    conda = conda_executable()
    requested_python_tuple()
    _prepare_prefix_for_conda()

    info = environment_python_info()
    if info is None:
        print(
            "Creating project-local Conda environment at {0} with Python {1}."
            .format(CONDA_PREFIX, CONDA_PYTHON_VERSION)
        )
        CONDA_PREFIX.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                conda,
                "create",
                "--yes",
                "--prefix",
                str(CONDA_PREFIX),
                "python={0}".format(CONDA_PYTHON_VERSION),
                "pip",
            ]
        )
        return

    version_data = info.get("version")
    actual: Optional[Tuple[int, int]] = None
    if isinstance(version_data, list) and len(version_data) >= 2:
        actual = (int(version_data[0]), int(version_data[1]))

    requested = requested_python_tuple()
    if actual != requested:
        shown = "unknown" if actual is None else "{0}.{1}".format(*actual)
        print(
            "Updating local Conda environment from Python {0} to Python {1}."
            .format(shown, CONDA_PYTHON_VERSION)
        )
        run_command(
            [
                conda,
                "install",
                "--yes",
                "--prefix",
                str(CONDA_PREFIX),
                "python={0}".format(CONDA_PYTHON_VERSION),
                "pip",
            ]
        )
    else:
        print(
            "Using existing local Conda environment at {0}."
            .format(CONDA_PREFIX)
        )


def setup() -> None:
    _create_or_update_environment()
    run_command(
        conda_run_command(
            "python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
        )
    )
    run_command(
        conda_run_command(
            "python",
            "-m",
            "pip",
            "install",
            "-r",
            "requirements-dev.txt",
        )
    )
    run_command(conda_run_command("python", "-m", "pip", "check"))
    info = require_environment()
    print("\nConda setup completed successfully.")
    print("Environment prefix: {0}".format(CONDA_PREFIX))
    print("Python executable: {0}".format(info.get("executable")))
    print("Project version: {0}".format(PROJECT_VERSION))
    print("Start the application with: make run")


def check_env() -> None:
    info = require_environment()
    print(
        "Project-local Conda environment ready: {0} ({1})"
        .format(CONDA_PREFIX, info.get("executable"))
    )


def require_runtime_python() -> None:
    requested = requested_python_tuple()
    actual = sys.version_info[:2]
    if actual != requested:
        raise RuntimeError(
            "This target requires Python {0} from {1}. Current interpreter: {2}"
            .format(CONDA_PYTHON_VERSION, CONDA_PREFIX, sys.executable)
        )
    if _normalized_path(sys.prefix) != _normalized_path(CONDA_PREFIX):
        raise RuntimeError(
            "This target must run inside the project-local Conda environment "
            "at {0}. Current prefix: {1}".format(CONDA_PREFIX, sys.prefix)
        )


def _tail_text_file(path: Path, line_count: int = 80) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


def run_app() -> None:
    require_runtime_python()
    command = [sys.executable, "-X", "faulthandler", "main.py"]
    print("\n> {0}".format(_display_command(command)), flush=True)

    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
    data_dir = Path(local_app_data) / "RoadMatcher"
    application_log = data_dir / "logs" / "road-matcher.log"
    native_log = data_dir / "native-crash.log"
    print("Console diagnostics are enabled.", flush=True)
    print("Application log: {0}".format(application_log), flush=True)
    print("Native crash log: {0}".format(native_log), flush=True)

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONFAULTHANDLER"] = "1"
    child_env["QT_FORCE_STDERR_LOGGING"] = "1"
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=child_env,
        check=False,
    )
    if result.returncode == 0:
        return

    unsigned_code = result.returncode & 0xFFFFFFFF
    windows_codes = {
        0xC0000005: "access violation",
        0xC0000409: "stack buffer overrun / fast-fail",
        0xC0000374: "heap corruption",
        0xC000001D: "illegal instruction",
    }
    description = windows_codes.get(unsigned_code, "unexpected process termination")
    print(
        "\nRoad Matcher exited abnormally: {0} (signed {1}, unsigned 0x{2:08X})."
        .format(description, result.returncode, unsigned_code),
        file=sys.stderr,
        flush=True,
    )

    application_tail = _tail_text_file(application_log)
    if application_tail:
        print("\n--- Last application log lines ---", file=sys.stderr)
        print(application_tail, file=sys.stderr)
    native_tail = _tail_text_file(native_log)
    if native_tail:
        print("\n--- Last native-fault log lines ---", file=sys.stderr)
        print(native_tail, file=sys.stderr)

    raise RuntimeError(
        "Road Matcher terminated with code 0x{0:08X} ({1}). Full logs: {2} and {3}."
        .format(unsigned_code, description, application_log, native_log)
    )


def test() -> None:
    require_runtime_python()
    run_command([sys.executable, "-m", "pytest"])


def verify() -> None:
    require_runtime_python()
    run_command([sys.executable, "-m", "pip", "check"])
    run_command([sys.executable, "-m", "pytest"])
    run_command(
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "app",
            "main.py",
            "scripts",
        ]
    )
    print("\nEnvironment, tests, and Python compilation checks passed.")


def verify_frozen_application() -> None:
    """Run the native GIS smoke test through the produced executable itself."""
    print("\n> {0} {1}".format(DIST_EXE, PACKAGING_SMOKE_TEST_ARGUMENT), flush=True)
    PACKAGING_SMOKE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    if PACKAGING_SMOKE_REPORT.exists():
        PACKAGING_SMOKE_REPORT.unlink()
    smoke_env = os.environ.copy()
    smoke_env["ROAD_MATCHER_PACKAGING_SMOKE_REPORT"] = str(PACKAGING_SMOKE_REPORT)
    try:
        result = subprocess.run(
            [str(DIST_EXE), PACKAGING_SMOKE_TEST_ARGUMENT],
            cwd=str(ROOT),
            env=smoke_env,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Frozen Road Matcher GIS smoke test timed out after 120 seconds."
        ) from exc
    if result.returncode != 0:
        details = _tail_text_file(PACKAGING_SMOKE_REPORT, line_count=120)
        detail_text = "" if not details else "\n\nFrozen smoke traceback:\n{0}".format(details)
        raise RuntimeError(
            "Frozen Road Matcher GIS smoke test failed with exit code {0}. "
            "The packaged application cannot safely be installed; verify Pyogrio/GDAL, "
            "PyProj, and Shapely native dependencies.{1}".format(
                result.returncode, detail_text
            )
        )
    print("Frozen GIS dependency smoke test passed.")


def build() -> None:
    require_runtime_python()
    test()
    run_command(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC_FILE),
        ]
    )
    if not DIST_EXE.exists():
        raise RuntimeError(
            "Build finished but the expected executable was not found: {0}"
            .format(DIST_EXE)
        )
    verify_frozen_application()
    print("\nStandalone application created at: {0}".format(DIST_EXE))


def _candidate_iscc_paths() -> Iterable[Path]:
    configured = os.environ.get("ISCC_EXE")
    if configured:
        yield Path(configured.strip().strip('"')).expanduser()

    for executable_name in ("ISCC.exe", "ISCC", "iscc"):
        resolved = shutil.which(executable_name)
        if resolved:
            yield Path(resolved)

    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        base_path = Path(base)
        yield base_path / "Inno Setup 6" / "ISCC.exe"
        yield base_path / "Programs" / "Inno Setup 6" / "ISCC.exe"


def find_iscc() -> Path:
    for candidate in _candidate_iscc_paths():
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6, "
        "put ISCC.exe on PATH, or run: make installer "
        'ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"'
    )


def compile_installer() -> None:
    require_runtime_python()
    if os.name != "nt":
        raise RuntimeError("The Inno Setup installer must be compiled on Windows.")
    if not DIST_EXE.exists():
        raise RuntimeError(
            "Standalone build not found at {0}. Run 'make build' first, or use "
            "'make installer'.".format(DIST_EXE)
        )
    iscc = find_iscc()
    run_command(
        [
            iscc,
            "/DMyAppVersion={0}".format(PROJECT_VERSION),
            str(INNO_SCRIPT),
        ]
    )
    if not INSTALLER_OUTPUT.exists():
        raise RuntimeError(
            "Inno Setup completed but the expected installer was not found: {0}"
            .format(INSTALLER_OUTPUT)
        )
    print("\nWindows installer created at: {0}".format(INSTALLER_OUTPUT))


def installer() -> None:
    build()
    compile_installer()


def _remove(path: Path) -> None:
    if path.is_dir():
        print("Removing directory: {0}".format(path))
        shutil.rmtree(str(path), ignore_errors=False)
    elif path.exists():
        print("Removing file: {0}".format(path))
        path.unlink()


def _is_inside_conda_prefix(path: Path) -> bool:
    try:
        path.resolve().relative_to(CONDA_PREFIX)
    except (ValueError, OSError):
        return False
    return True


def clean() -> None:
    for relative in ("build", "dist", "installer_output", ".pytest_cache"):
        _remove(ROOT / relative)

    cache_dirs = [
        path
        for path in ROOT.rglob("__pycache__")
        if not _is_inside_conda_prefix(path)
    ]
    for cache_dir in sorted(cache_dirs, reverse=True):
        _remove(cache_dir)

    compiled_files = [
        path
        for pattern in ("*.pyc", "*.pyo")
        for path in ROOT.rglob(pattern)
        if not _is_inside_conda_prefix(path)
    ]
    for compiled_file in compiled_files:
        _remove(compiled_file)

    print("\nBuild artifacts and project Python caches removed.")


def remove_conda_environment() -> None:
    if CONDA_HISTORY.is_file():
        run_command(
            [
                conda_executable(),
                "env",
                "remove",
                "--yes",
                "--prefix",
                str(CONDA_PREFIX),
            ]
        )

    if CONDA_PREFIX.exists():
        _remove(CONDA_PREFIX)
    else:
        print("Local Conda environment is already absent: {0}".format(CONDA_PREFIX))


def distclean() -> None:
    clean()
    remove_conda_environment()
    print(
        "\nThe project-local Conda environment and generated build files were "
        "removed. Source code and user output files were preserved."
    )


def rebuild() -> None:
    require_runtime_python()
    clean()
    build()


def doctor() -> None:
    conda = conda_executable()
    requested_python_tuple()
    version_result = capture_command([conda, "--version"])
    print("Road Matcher environment diagnostics")
    print("Project root: {0}".format(ROOT))
    print("Project version: {0}".format(PROJECT_VERSION))
    print("Conda executable: {0}".format(conda))
    print("Conda version: {0}".format(version_result.stdout.strip()))
    print("Configured local prefix: {0}".format(CONDA_PREFIX))
    print("Configured Python: {0}".format(CONDA_PYTHON_VERSION))
    print("Task runner exists: {0}".format(Path(__file__).is_file()))
    print("Conda marker exists: {0}".format(CONDA_HISTORY.is_file()))

    info = environment_python_info()
    if info is None:
        print("Environment status: NOT CREATED")
        print("Next command: make setup")
        return

    version_data = info.get("version")
    version_text = "unknown"
    if isinstance(version_data, list):
        version_text = ".".join(str(value) for value in version_data)
    print("Environment status: READY")
    print("Environment Python version: {0}".format(version_text))
    print("Environment Python executable: {0}".format(info.get("executable")))
    print("Environment prefix: {0}".format(info.get("prefix")))


def show_help() -> None:
    print(
        """Road Matcher Desktop project-local Conda tasks

  make setup           Create/update ./.venv with Conda
  make run             Launch the PySide6 desktop application
  make test            Run the automated test suite
  make verify          Run pip check, tests, and compilation checks
  make build           Test and create dist/RoadMatcher[.exe]
  make installer       Build and compile the Inno Setup installer
  make installer-only  Compile installer from an existing app build
  make clean           Remove build outputs and project Python caches
  make distclean       Clean and remove the local ./.venv environment
  make rebuild         Clean, test, and rebuild the standalone application
  make doctor          Show Conda/local-environment diagnostics
  make help            Show this command list

No manual conda activation and no py/python launcher selection are required.
"""
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "task",
        choices=(
            "help",
            "check-env",
            "doctor",
            "setup",
            "run",
            "test",
            "verify",
            "build",
            "installer",
            "installer-only",
            "clean",
            "distclean",
            "rebuild",
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    actions = {
        "help": show_help,
        "check-env": check_env,
        "doctor": doctor,
        "setup": setup,
        "run": run_app,
        "test": test,
        "verify": verify,
        "build": build,
        "installer": installer,
        "installer-only": compile_installer,
        "clean": clean,
        "distclean": distclean,
        "rebuild": rebuild,
    }
    try:
        actions[args.task]()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        print("\nERROR: {0}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
