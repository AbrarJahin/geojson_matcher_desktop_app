from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

_LOG_STREAM: IO[str] | None = None
_QT_HANDLER: Any = None


def application_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    directory = base / "RoadMatcher"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def configure_logging() -> tuple[Path, Path]:
    global _LOG_STREAM, _QT_HANDLER

    data_dir = application_data_dir()
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    application_log = log_dir / "road-matcher.log"
    native_log = data_dir / "native-crash.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)s | %(name)s | %(message)s"
    )
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        application_log,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    try:
        _LOG_STREAM = native_log.open("a", encoding="utf-8", buffering=1)
        _LOG_STREAM.write("\n--- Road Matcher process started ---\n")
        faulthandler.enable(file=_LOG_STREAM, all_threads=True)
    except Exception:
        logging.getLogger(__name__).exception("Could not open the native crash log.")
        _LOG_STREAM = None
        faulthandler.enable(all_threads=True)

    def uncaught_exception(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        logging.getLogger("uncaught").critical(
            "Uncaught Python exception", exc_info=(exc_type, exc, tb)
        )

    def thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("threading").critical(
            "Uncaught exception in thread %s",
            args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def unraisable(unraisable_args: Any) -> None:
        logging.getLogger("unraisable").critical(
            "Unraisable exception in %r: %s",
            unraisable_args.object,
            unraisable_args.err_msg,
            exc_info=(
                type(unraisable_args.exc_value),
                unraisable_args.exc_value,
                unraisable_args.exc_traceback,
            ),
        )

    sys.excepthook = uncaught_exception
    threading.excepthook = thread_exception
    sys.unraisablehook = unraisable

    qt_logger = logging.getLogger("Qt")

    def qt_message_handler(mode: QtMsgType, context: Any, message: str) -> None:
        location = ""
        if context is not None:
            file_name = getattr(context, "file", None)
            line = getattr(context, "line", None)
            function = getattr(context, "function", None)
            pieces = [str(value) for value in (file_name, line, function) if value]
            if pieces:
                location = " [" + ":".join(pieces) + "]"
        if mode == QtMsgType.QtDebugMsg:
            qt_logger.debug("%s%s", message, location)
        elif mode == QtMsgType.QtInfoMsg:
            qt_logger.info("%s%s", message, location)
        elif mode == QtMsgType.QtWarningMsg:
            qt_logger.warning("%s%s", message, location)
        elif mode == QtMsgType.QtCriticalMsg:
            qt_logger.error("%s%s", message, location)
        elif mode == QtMsgType.QtFatalMsg:
            qt_logger.critical("%s%s", message, location)
        else:
            qt_logger.info("%s%s", message, location)

    _QT_HANDLER = qt_message_handler
    qInstallMessageHandler(_QT_HANDLER)

    logger = logging.getLogger(__name__)
    logger.info("Application log: %s", application_log)
    logger.info("Native crash log: %s", native_log)
    logger.info(
        "Runtime: Python %s | PySide6 %s | %s %s",
        platform.python_version(),
        pyside_version,
        platform.system(),
        platform.release(),
    )
    return application_log, native_log


class GuardedApplication(QApplication):
    """Log Python exceptions raised while Qt dispatches an event."""

    def notify(self, receiver: Any, event: Any) -> bool:
        try:
            return super().notify(receiver, event)
        except Exception:
            logging.getLogger("Qt.notify").exception(
                "Python exception while dispatching Qt event %r to %r", event, receiver
            )
            return False
