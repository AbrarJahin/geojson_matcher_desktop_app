# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Scientific/GIS packages carry CRS databases, projection grids, and native
# libraries that must remain beside the executable.
datas = []
for package in ["geopandas", "pyproj", "matplotlib"]:
    datas += collect_data_files(package, include_py_files=False)

# Application branding used by Qt at runtime.
datas += [("app/resources/road_matcher.png", "app/resources")]

binaries = []
for package in ["pyproj", "pyogrio", "shapely"]:
    binaries += collect_dynamic_libs(package)

hiddenimports = [
    "pyogrio._io",
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
