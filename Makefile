.DEFAULT_GOAL := help

# Override from the command line when needed, for example:
#   make setup PYTHON="py -3.12"
#   make setup PYTHON=python
ifeq ($(OS),Windows_NT)
PYTHON ?= py
else
PYTHON ?= python3
endif

TASK_RUNNER := scripts/project_tasks.py

# Allows this value to be supplied as a make command-line variable:
#   make installer ISCC_EXE="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
export ISCC_EXE

.PHONY: help setup run test verify build installer installer-only clean distclean rebuild

help:
	@$(PYTHON) $(TASK_RUNNER) help

setup:
	@$(PYTHON) $(TASK_RUNNER) setup

run:
	@$(PYTHON) $(TASK_RUNNER) run

test:
	@$(PYTHON) $(TASK_RUNNER) test

verify:
	@$(PYTHON) $(TASK_RUNNER) verify

build:
	@$(PYTHON) $(TASK_RUNNER) build

installer:
	@$(PYTHON) $(TASK_RUNNER) installer

installer-only:
	@$(PYTHON) $(TASK_RUNNER) installer-only

clean:
	@$(PYTHON) $(TASK_RUNNER) clean

distclean:
	@$(PYTHON) $(TASK_RUNNER) distclean

rebuild:
	@$(PYTHON) $(TASK_RUNNER) rebuild
