.DEFAULT_GOAL := help

# Project-local Conda environment. The environment is created inside this
# repository at ./.venv; it is not a global named Conda environment.
# On the reported Windows installation, prefer the concrete Miniconda path;
# otherwise fall back to resolving `conda` from PATH.
ifeq ($(OS),Windows_NT)
ifneq ($(wildcard C:/tools/miniconda3/Scripts/conda.exe),)
CONDA ?= C:/tools/miniconda3/Scripts/conda.exe
else
CONDA ?= conda
endif
else
CONDA ?= conda
endif
ENV_PREFIX ?= $(CURDIR)/.venv
PYTHON_VERSION ?= 3.12
TASK_RUNNER := scripts/project_tasks.py

ifeq ($(OS),Windows_NT)
NULL_DEVICE := NUL
BLANK_LINE = @echo.
else
NULL_DEVICE := /dev/null
BLANK_LINE = @echo
endif

# Values consumed by scripts/project_tasks.py.
export ROAD_MATCHER_CONDA_PREFIX := $(ENV_PREFIX)
export ROAD_MATCHER_PYTHON_VERSION := $(PYTHON_VERSION)
export ROAD_MATCHER_CONDA_EXE := $(CONDA)
export ISCC_EXE

# Bootstrap tasks run with Conda's base Python. Application tasks run with the
# Python interpreter stored in the project-local Conda prefix.
BASE_PYTHON := "$(CONDA)" run --no-capture-output --name base python
ENV_PYTHON := "$(CONDA)" run --no-capture-output --prefix "$(ENV_PREFIX)" python

.PHONY: help check-conda check-env doctor setup run test verify build \
        installer installer-only publish clean distclean rebuild

help:
	@echo Road Matcher Desktop - project-local Conda commands
	$(BLANK_LINE)
	@echo   make setup           Create/update .venv with Conda and install dependencies
	@echo   make run             Start the PySide6 desktop application
	@echo   make test            Run the automated tests
	@echo   make verify          Check dependencies, tests, and Python compilation
	@echo   make build           Create dist/RoadMatcher.exe
	@echo   make installer       Build the app and create the Windows installer
	@echo   make installer-only  Create installer from an existing app build
	@echo   make publish         Test first; publish installer only if tests pass
	@echo   make clean           Remove build outputs and project Python caches
	@echo   make distclean       Clean and remove the local .venv Conda environment
	@echo   make rebuild         Clean, test, and rebuild the application
	@echo   make doctor          Diagnose Conda and the local environment
	$(BLANK_LINE)
	@echo Configuration:
	@echo   Conda command:       $(CONDA)
	@echo   Environment prefix:  $(ENV_PREFIX)
	@echo   Python version:      $(PYTHON_VERSION)
	$(BLANK_LINE)
	@echo Examples:
	@echo   make setup
	@echo   make setup CONDA="C:/tools/miniconda3/Scripts/conda.exe"
	@echo   make installer ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
	@echo   make publish ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"

check-conda:
	@"$(CONDA)" --version >$(NULL_DEVICE) 2>&1 || (echo ERROR: Conda command "$(CONDA)" was not found. && echo Use the full executable path, for example: && echo make setup CONDA="C:/tools/miniconda3/Scripts/conda.exe" && exit 1)

check-env: check-conda
	@$(BASE_PYTHON) $(TASK_RUNNER) check-env

doctor: check-conda
	@$(BASE_PYTHON) $(TASK_RUNNER) doctor

setup: check-conda
	@$(BASE_PYTHON) $(TASK_RUNNER) setup

run: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) run

test: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) test

verify: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) verify

build: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) build

installer: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) installer

installer-only: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) installer-only

# "publish" is intentionally a fail-closed release gate. The first substantive
# operation is the complete unit-test command. If it fails, Make exits here and
# the installer/build command is never invoked. The existing installer command
# retains its own internal test/build/smoke-test checks as a second safety net.
publish: check-env
	@echo Publish gate: running the complete unit-test suite before packaging...
	@$(ENV_PYTHON) $(TASK_RUNNER) test || (echo PUBLISH FAILED: Unit tests failed. No publish/build step was started. && exit 1)
	@echo Publish gate passed: all unit tests succeeded. Building the release installer...
	@$(ENV_PYTHON) $(TASK_RUNNER) installer || (echo PUBLISH FAILED: Release packaging failed after the tests passed. && exit 1)
	@echo PUBLISH SUCCEEDED: All unit tests passed and the Windows installer was created.

clean: check-conda
	@$(BASE_PYTHON) $(TASK_RUNNER) clean

distclean: check-conda
	@$(BASE_PYTHON) $(TASK_RUNNER) distclean

rebuild: check-env
	@$(ENV_PYTHON) $(TASK_RUNNER) rebuild
