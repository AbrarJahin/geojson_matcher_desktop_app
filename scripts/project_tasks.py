"""Makefile task implementation for Road Matcher Desktop.

The Makefile intentionally delegates filesystem and process work to Python so
that the same targets work from Windows Command Prompt, PowerShell, Git Bash,
and common Unix shells without shell-specific activation commands.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if os.name == "nt"
    else VENV_DIR / "bin" / "python"
)
SPEC_FILE = ROOT / "road_matcher.spec"
INNO_SCRIPT = ROOT / "installer" / "RoadMatcher.iss"
DIST_EXE = ROOT / "dist" / "RoadMatcher" / (
    "RoadMatcher.exe" if os.name == "nt" else "RoadMatcher"
)
INSTALLER_OUTPUT = ROOT / "installer_output" / "RoadMatcher-Setup-1.0.0.exe"


def _display_command(command: Sequence[str | os.PathLike[str]]) -> str:
    return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)


def run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    printable = _display_command(command)
    print(f"\n> {printable}", flush=True)
    subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=True,
    )


def require_supported_python() -> None:
    if not ((3, 10) <= sys.version_info[:2] < (3, 15)):
        raise RuntimeError(
            "Road Matcher requires Python 3.10 through 3.14. "
            f"The Make task is currently using {sys.version.split()[0]}. "
            "On Windows, try: make setup PYTHON=\"py -3.12\""
        )


def require_venv() -> Path:
    if not VENV_PYTHON.is_file():
        raise RuntimeError(
            f"Virtual environment not found at {VENV_DIR}. Run 'make setup' first."
        )
    return VENV_PYTHON


def setup() -> None:
    require_supported_python()
    if not VENV_PYTHON.is_file():
        print(f"Creating virtual environment: {VENV_DIR}")
        run_command([sys.executable, "-m", "venv", str(VENV_DIR)])
    else:
        print(f"Using existing virtual environment: {VENV_DIR}")

    python = require_venv()
    run_command([python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run_command([python, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    run_command([python, "-m", "pip", "check"])
    print("\nSetup completed. Start the application with: make run")


def run_app() -> None:
    python = require_venv()
    run_command([python, "main.py"])


def test() -> None:
    python = require_venv()
    run_command([python, "-m", "pytest"])


def verify() -> None:
    python = require_venv()
    run_command([python, "-m", "pip", "check"])
    run_command([python, "-m", "pytest"])
    run_command([python, "-m", "compileall", "-q", "app", "main.py", "scripts"])
    print("\nEnvironment, tests, and Python compilation checks passed.")


def build() -> None:
    python = require_venv()
    test()
    run_command(
        [
            python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC_FILE),
        ]
    )
    if not DIST_EXE.exists():
        raise RuntimeError(f"Build finished but expected executable was not found: {DIST_EXE}")
    print(f"\nStandalone application created at: {DIST_EXE}")


def _candidate_iscc_paths() -> Iterable[Path]:
    configured = os.environ.get("ISCC_EXE")
    if configured:
        yield Path(configured).expanduser()

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
        "ensure ISCC.exe is on PATH, or set ISCC_EXE to its full path. Example: "
        "make installer ISCC_EXE=\"C:/Program Files (x86)/Inno Setup 6/ISCC.exe\""
    )


def compile_installer() -> None:
    if os.name != "nt":
        raise RuntimeError("The Inno Setup installer must be compiled on Windows.")
    if not DIST_EXE.exists():
        raise RuntimeError(
            f"Standalone build not found at {DIST_EXE}. Run 'make build' first, "
            "or run 'make installer' to build and package in one command."
        )
    iscc = find_iscc()
    run_command([iscc, str(INNO_SCRIPT)])
    if not INSTALLER_OUTPUT.exists():
        raise RuntimeError(
            "Inno Setup completed but the expected installer was not found at "
            f"{INSTALLER_OUTPUT}"
        )
    print(f"\nWindows installer created at: {INSTALLER_OUTPUT}")


def installer() -> None:
    build()
    compile_installer()


def _remove(path: Path) -> None:
    if path.is_dir():
        print(f"Removing directory: {path}")
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists():
        print(f"Removing file: {path}")
        path.unlink()


def _is_inside_virtual_environment(path: Path) -> bool:
    try:
        path.relative_to(VENV_DIR)
    except ValueError:
        return False
    return True


def clean() -> None:
    for relative in ("build", "dist", "installer_output", ".pytest_cache"):
        _remove(ROOT / relative)

    # Keep normal cleanup fast by not traversing/deleting caches inside .venv.
    # The complete virtual environment is removed once by the distclean target.
    cache_dirs = [
        path
        for path in ROOT.rglob("__pycache__")
        if not _is_inside_virtual_environment(path)
    ]
    for cache_dir in sorted(cache_dirs, reverse=True):
        _remove(cache_dir)

    compiled_files = [
        path
        for pattern in ("*.pyc", "*.pyo")
        for path in ROOT.rglob(pattern)
        if not _is_inside_virtual_environment(path)
    ]
    for compiled_file in compiled_files:
        _remove(compiled_file)

    print("\nBuild artifacts and project Python caches removed.")


def distclean() -> None:
    clean()
    _remove(VENV_DIR)
    print("\nVirtual environment also removed. Source code and user output files were preserved.")


def rebuild() -> None:
    clean()
    build()


def show_help() -> None:
    print(
        """Road Matcher Desktop Make targets

  make setup           Create .venv and install runtime/build/test dependencies
  make run             Launch the PySide6 desktop application
  make test            Run the automated test suite
  make verify          Run pip check, tests, and Python compilation checks
  make build           Test and create dist/RoadMatcher/RoadMatcher.exe
  make installer       Build the application and compile the Inno Setup installer
  make installer-only  Compile the installer from an existing standalone build
  make clean           Remove build outputs and Python caches
  make distclean       Run clean and also remove .venv
  make rebuild         Clean, test, and rebuild the standalone application
  make help            Show this command list

Windows notes:
  - GNU Make must be available as 'make'. Some toolchains expose it as
    'mingw32-make'; use the same target names with that command.
  - The default Windows interpreter command is: py
  - Override it when needed, for example:
      make setup PYTHON=python
      make setup PYTHON=\"C:/Python312/python.exe\"
  - Installer creation requires Inno Setup 6. If it is not auto-detected:
      make installer ISCC_EXE=\"C:/Program Files (x86)/Inno Setup 6/ISCC.exe\"
"""
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "task",
        choices=(
            "help",
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
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
