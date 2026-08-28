# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_dynamic_libs,
)

# Scientific/GIS packages carry CRS databases, projection grids, and native
# libraries that must remain beside the executable.
datas = []
for package in ["geopandas", "pyproj", "matplotlib"]:
    datas += collect_data_files(package, include_py_files=False)

# Application branding used by Qt at runtime.  Bundle the ICO as data too so
# Windows can use the same multi-resolution icon for the live taskbar window.
datas += [
    ("app/resources/road_matcher.png", "app/resources"),
    ("app/resources/road_matcher.ico", "app/resources"),
]

binaries = []
for package in ["pyproj", "shapely"]:
    binaries += collect_dynamic_libs(package)

# GeoPandas loads Pyogrio dynamically.  Collect the complete package rather
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
    [],
    exclude_binaries=True,
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RoadMatcher",
)
