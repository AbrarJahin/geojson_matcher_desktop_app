from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _publish_recipe() -> str:
    lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines)
        if line.startswith("publish:")
    )
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(("\t", " ", "#")) and ":" in line:
            break
        body.append(line)
    return "\n".join(body)


def test_publish_runs_tests_before_any_installer_command() -> None:
    recipe = _publish_recipe()
    test_position = recipe.index("$(TASK_RUNNER) test")
    installer_position = recipe.index("$(TASK_RUNNER) installer")

    assert test_position < installer_position
    assert "PUBLISH FAILED: Unit tests failed." in recipe
    assert "No publish/build step was started." in recipe


def test_publish_is_fail_closed_and_reports_packaging_failure() -> None:
    recipe = _publish_recipe()

    test_line = next(
        line for line in recipe.splitlines()
        if "$(TASK_RUNNER) test" in line
    )
    installer_line = next(
        line for line in recipe.splitlines()
        if "$(TASK_RUNNER) installer" in line
    )

    assert "||" in test_line
    assert "exit 1" in test_line
    assert "||" in installer_line
    assert "exit 1" in installer_line
    assert "PUBLISH FAILED: Release packaging failed" in installer_line


def test_make_help_advertises_publish_command() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "make publish" in text
    assert "installer installer-only publish clean" in text
