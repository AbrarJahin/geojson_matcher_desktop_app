from __future__ import annotations

import logging
import os
import sys
import ctypes
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from app import __version__
from app.logging_setup import GuardedApplication, configure_logging
from app.ui.main_window import MainWindow


WINDOWS_APP_USER_MODEL_ID = "RoadMatcher.Research.Desktop"
PACKAGING_SMOKE_TEST_ARGUMENT = "--packaging-smoke-test"


def _set_windows_app_identity() -> None:
    """Give Windows a stable taskbar identity before Qt creates any windows."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            WINDOWS_APP_USER_MODEL_ID
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not set the Windows AppUserModelID; continuing normally."
        )


def _application_icon_path() -> Path:
    """Return the best bundled Road Matcher icon for this platform."""

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    resources = bundle_root / "app" / "resources"
    ico_path = resources / "road_matcher.ico"
    if os.name == "nt" and ico_path.is_file():
        return ico_path
    return resources / "road_matcher.png"


def _run_packaging_smoke_test() -> int:
    """Exercise native GIS dependencies without opening the desktop UI.

    The build pipeline runs this function through the frozen executable.  It
    deliberately covers GeoPandas -> Pyogrio/GDAL input, Shapely geometry, and
    PyProj CRS data so a PyInstaller build cannot pass merely because the EXE
    file exists.
    """
    import geopandas as gpd
    import pyogrio  # noqa: F401 - explicit import is part of the packaging check

    sample = (
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"id":1},'
        '"geometry":{"type":"LineString","coordinates":'
        '[[-86.16,39.77],[-86.15,39.78]]}}]}'
    )
    with tempfile.TemporaryDirectory(prefix="road-matcher-smoke-") as directory:
        source = Path(directory) / "packaging-smoke.geojson"
        source.write_text(sample, encoding="utf-8")
        frame = gpd.read_file(source, engine="pyogrio")
        if len(frame) != 1 or frame.geometry.iloc[0].geom_type != "LineString":
            raise RuntimeError("Frozen GIS smoke test could not read the sample GeoJSON.")
        projected = frame.to_crs("EPSG:26916")
        if projected.crs is None or projected.geometry.iloc[0].length <= 0:
            raise RuntimeError("Frozen GIS smoke test could not project sample geometry.")
    return 0


def main() -> int:
    if PACKAGING_SMOKE_TEST_ARGUMENT in sys.argv:
        try:
            return _run_packaging_smoke_test()
        except Exception:
            # A windowed PyInstaller executable must fail the build without
            # presenting an interactive crash dialog. Preserve the traceback
            # for the build task when it supplied a report path.
            report_path = os.environ.get("ROAD_MATCHER_PACKAGING_SMOKE_REPORT")
            if report_path:
                try:
                    report = Path(report_path)
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text(traceback.format_exc(), encoding="utf-8")
                except OSError:
                    pass
            return 86

    application_log, native_log = configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Road Matcher %s", __version__)

    GuardedApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    _set_windows_app_identity()
    application = GuardedApplication(sys.argv)
    application.setApplicationName("Road Matcher")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("Road Matcher Research")
    application.setOrganizationDomain("roadmatcher.local")

    icon_path = _application_icon_path()
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    else:
        logger.warning("Application icon was not found: %s", icon_path)

    logger.info("Console logging is active.")
    logger.info("Detailed log file: %s", application_log)
    logger.info("Native fault log: %s", native_log)

    window = MainWindow()
    window.show()
    exit_code = application.exec()
    logger.info("Qt event loop exited with code %s", exit_code)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
