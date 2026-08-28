from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_pyinstaller_spec_collects_complete_pyogrio_and_windows_wheel_libs() -> None:
    spec_text = (ROOT / "road_matcher.spec").read_text(encoding="utf-8")

    assert 'collect_all("pyogrio")' in spec_text
    assert 'collect_delvewheel_libs_directory' in spec_text
    assert '["pyogrio", "pyproj", "shapely"]' in spec_text


def test_packaging_smoke_test_covers_real_geojson_io_and_projection() -> None:
    # Import here so the release-configuration checks above remain useful even
    # if a developer has not yet loaded Qt while diagnosing a build machine.
    from main import _run_packaging_smoke_test

    assert _run_packaging_smoke_test() == 0
