from __future__ import annotations

import ast
from collections import defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _runtime_imports(path: Path, known_modules: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name in known_modules:
                    imported.add(name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in known_modules:
                imported.add(node.module)

    return imported


def test_every_application_module_is_reachable_from_main_entrypoint() -> None:
    """Fail when a new app/*.py module is committed but never used at runtime."""

    app_files = sorted(APP_ROOT.rglob("*.py"))
    module_to_file = {_module_name(path): path for path in app_files}
    known_modules = set(module_to_file)

    graph: dict[str, set[str]] = defaultdict(set)
    graph["__entrypoint__"] = _runtime_imports(ROOT / "main.py", known_modules)
    for module, path in module_to_file.items():
        graph[module] = _runtime_imports(path, known_modules)

    reachable: set[str] = set()
    queue = deque(graph["__entrypoint__"])
    while queue:
        module = queue.popleft()
        if module in reachable:
            continue
        reachable.add(module)
        queue.extend(graph[module] - reachable)

    # __init__.py package modules are packaging metadata and need not be
    # explicitly imported by a child module to be legitimate.
    package_modules = {
        _module_name(path)
        for path in app_files
        if path.name == "__init__.py"
    }
    runtime_modules = known_modules - package_modules
    unreachable = sorted(runtime_modules - reachable)
    assert unreachable == [], (
        "Application Python modules not reachable from main.py: "
        + ", ".join(unreachable)
    )


def test_updated_map_window_and_base_main_window_are_both_in_runtime_graph() -> None:
    """Document the intentional two-layer window architecture."""

    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    updated_text = (APP_ROOT / "ui" / "updated_map_window.py").read_text(
        encoding="utf-8"
    )

    assert "from app.ui.updated_map_window import MainWindow" in main_text
    assert (
        "from app.ui.main_window import MainWindow as BaseMainWindow"
        in updated_text
    )
    assert "class MainWindow(BaseMainWindow):" in updated_text


def test_disabled_workflow_backup_is_not_required_by_runtime_code() -> None:
    """The .old workflow is archival only and safe to delete."""

    old_name = "tests.yml.old"
    source_files = [
        ROOT / "main.py",
        *APP_ROOT.rglob("*.py"),
        *(ROOT / "scripts").rglob("*.py"),
        ROOT / "Makefile",
        ROOT / "pyproject.toml",
    ]
    for path in source_files:
        assert old_name not in path.read_text(encoding="utf-8")
