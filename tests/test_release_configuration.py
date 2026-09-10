from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
        text,
    )
    assert match is not None
    return match.group(1)


def test_installer_shortcuts_use_same_windows_app_identity_as_process() -> None:
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    installer_text = (ROOT / "installer" / "RoadMatcher.iss").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r'WINDOWS_APP_USER_MODEL_ID\s*=\s*"([^"]+)"', main_text
    )
    assert match is not None
    identity = match.group(1)

    assert f'#define MyAppUserModelID "{identity}"' in installer_text
    assert installer_text.count('AppUserModelID: "{#MyAppUserModelID}"') == 2
    assert installer_text.count(
        'IconFilename: "{app}\\{#MyAppExeName}"'
    ) == 2


def test_pyinstaller_spec_collects_complete_native_runtime_and_macos_bundle() -> None:
    spec_text = (ROOT / "road_matcher.spec").read_text(encoding="utf-8")
    installer_text = (ROOT / "installer" / "RoadMatcher.iss").read_text(
        encoding="utf-8"
    )

    assert 'collect_all("pyogrio")' in spec_text
    assert "collect_delvewheel_libs_directory" in spec_text
    assert '["pyogrio", "pyproj", "shapely"]' in spec_text
    assert '("pyproject.toml", ".")' in spec_text
    assert "libexpat.so.1" in spec_text
    assert 'if sys.platform == "darwin":' in spec_text
    assert "BUNDLE(" in spec_text
    assert 'name="Road Matcher.app"' in spec_text
    assert "CFBundleShortVersionString" in spec_text

    assert "exclude_binaries=True" not in spec_text
    assert "COLLECT(" not in spec_text
    assert "a.binaries," in spec_text
    assert "a.datas," in spec_text

    assert 'Source: "..\\dist\\RoadMatcher.exe"' in installer_text
    assert "recursesubdirs" not in installer_text
    assert 'Name: "{app}\\_internal"' in installer_text
    assert 'Name: "{app}\\*.pyd"' in installer_text
    assert 'Name: "{app}\\python*.dll"' in installer_text


def test_pyproject_is_single_active_version_source() -> None:
    version = _project_version()

    app_text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    tasks_text = (ROOT / "scripts" / "project_tasks.py").read_text(
        encoding="utf-8"
    )
    installer_text = (ROOT / "installer" / "RoadMatcher.iss").read_text(
        encoding="utf-8"
    )
    map_text = (ROOT / "app" / "ui" / "map_canvas.py").read_text(
        encoding="utf-8"
    )
    spec_text = (ROOT / "road_matcher.spec").read_text(encoding="utf-8")
    release_text = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    citation_text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")

    # Historical documentation/changelog entries are not version sources.
    # Runtime/build/release code must never carry a second current-version
    # literal.
    assert version not in app_text
    assert version not in tasks_text
    assert version not in installer_text
    assert version not in map_text
    assert version not in spec_text
    assert version not in release_text
    assert version not in citation_text

    assert "_version_from_pyproject" in app_text
    assert "project_version()" in tasks_text
    assert '--define=MyAppVersion="{0}"' in tasks_text
    assert "#ifndef MyAppVersion" in installer_text
    assert "from app import __version__" in map_text
    assert "RoadMatcherDesktop/{__version__}" in map_text


def test_release_workflow_shows_version_in_build_and_publish_jobs() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert (
        "name: Build ${{ matrix.label }} · "
        "v${{ needs.gate.outputs.version }}"
    ) in workflow
    assert (
        "name: Publish GitHub Release · "
        "v${{ needs.gate.outputs.version }}"
    ) in workflow
    assert "Road Matcher version: ${PROJECT_VERSION}" in workflow
    assert "Road Matcher version: $env:PROJECT_VERSION" in workflow


def test_release_workflow_builds_standard_platform_installers() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "Windows x86-64" in workflow
    assert "Ubuntu x86-64" in workflow
    assert "macOS Apple Silicon" in workflow
    assert "macOS Intel x86-64" in workflow

    assert "asset_extension: .exe" in workflow
    assert "asset_extension: .deb" in workflow
    assert workflow.count("asset_extension: .dmg") == 2

    assert "dpkg-deb --build --root-owner-group" in workflow
    assert "Architecture: amd64" in workflow
    assert "road-matcher.desktop" in workflow

    assert "hdiutil create" in workflow
    assert "Road Matcher.app" in workflow
    assert "ln -s /Applications" in workflow

    assert "RoadMatcher-Windows-x86_64-" in workflow
    assert "RoadMatcher-Ubuntu-x86_64-" in workflow
    assert "RoadMatcher-macOS-arm64-" in workflow
    assert "RoadMatcher-macOS-x86_64-" in workflow


def test_release_remains_manual_and_requires_successful_master_tests() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "actions/workflows/tests.yml/runs" in workflow
    assert "head_sha=${MASTER_SHA}" in workflow
    assert 'if [[ "$LATEST_CONCLUSION" != "success" ]]' in workflow


def test_packaging_smoke_test_covers_real_geojson_io_and_projection() -> None:
    # Import here so the release-configuration checks above remain useful even
    # if a developer has not yet loaded Qt while diagnosing a build machine.
    from main import _run_packaging_smoke_test

    assert _run_packaging_smoke_test() == 0
