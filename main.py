from __future__ import annotations

import logging
import os
import sys
import ctypes
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from app import __version__
from app.logging_setup import GuardedApplication, configure_logging
from app.ui.main_window import MainWindow


WINDOWS_APP_USER_MODEL_ID = "RoadMatcher.Research.Desktop"


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


def main() -> int:
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
