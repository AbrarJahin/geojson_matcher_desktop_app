# -*- mode: python ; coding: utf-8 -*-
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_dynamic_libs,
)

# pyproject.toml is the single source of truth for the application version.
_pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
_version_match = re.search(
    r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
    _pyproject_text,
)
if _version_match is None:
    raise RuntimeError("Could not read the application version from pyproject.toml.")
APP_VERSION = _version_match.group(1).strip()

# Scientific/GIS packages carry CRS databases, projection grids, and native
# libraries that must remain together when the one-file app extracts them.
datas = []
for package in ["geopandas", "pyproj", "matplotlib"]:
    datas += collect_data_files(package, include_py_files=False)

# Application branding plus the single version-source file. The frozen app
# reads this bundled pyproject.toml when it reports its version.
datas += [
    ("app/resources/road_matcher.png", "app/resources"),
    ("app/resources/road_matcher.ico", "app/resources"),
    ("pyproject.toml", "."),
]

binaries = []
for package in ["pyproj", "shapely"]:
    binaries += collect_dynamic_libs(package)

# Conda Python's pyexpat extension can be linked against a newer Expat than
# the Linux host provides. The source interpreter works because its matching
# libexpat lives inside the Conda prefix; bundle that exact library so the
# frozen executable resolves the same ABI instead of an older system libexpat.
if sys.platform.startswith("linux"):
    expat_candidates = [
        Path(sys.prefix) / "lib" / "libexpat.so.1",
        *sorted((Path(sys.prefix) / "lib").glob("libexpat.so.1.*")),
    ]
    conda_expat = next(
        (candidate for candidate in expat_candidates if candidate.is_file()),
        None,
    )
    if conda_expat is None:
        raise RuntimeError(
            "The active Linux Conda environment does not contain libexpat.so.1; "
            "refusing to build a potentially broken frozen application."
        )
    binaries.append((str(conda_expat), "."))

# GeoPandas loads Pyogrio dynamically. Collect the complete package rather
# than only pyogrio._io so its Python modules and GDAL data are retained.
pyogrio_datas, pyogrio_binaries, pyogrio_hiddenimports = collect_all("pyogrio")
datas += pyogrio_datas
binaries += pyogrio_binaries

# Windows wheels built with delvewheel keep dependent native DLLs in sibling
# <package>.libs directories, outside collect_dynamic_libs()' normal scope.
for package in ["pyogrio", "pyproj", "shapely"]:
    datas, binaries = collect_delvewheel_libs_directory(
        package, datas=datas, binaries=binaries
    )

hiddenimports = pyogrio_hiddenimports + [
    "sklearn.mixture._gaussian_mixture",
    "sklearn.decomposition._pca",
    "sklearn.preprocessing._data",
    "matplotlib.backends.backend_qtagg",
    "PySide6.QtNetwork",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "notebook",
        "google.colab",
        "ipywidgets",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "pytest",
        "contextily",
        "rasterio",
        "xyzservices",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RoadMatcher",
    icon="app/resources/road_matcher.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# On macOS, wrap the same frozen application in the standard .app bundle
# expected inside a drag-and-drop DMG. No application logic changes.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Road Matcher.app",
        icon="app/resources/road_matcher.ico",
        bundle_identifier="me.ajahin.roadmatcher",
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": "Road Matcher",
            "CFBundleName": "Road Matcher",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
