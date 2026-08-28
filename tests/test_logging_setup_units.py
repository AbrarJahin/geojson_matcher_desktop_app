from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QtMsgType

import app.logging_setup as logging_setup


def test_application_data_dir_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    directory = logging_setup.application_data_dir()
    assert directory == tmp_path / "RoadMatcher"
    assert directory.is_dir()


def test_configure_logging_creates_logs_hooks_and_qt_handler(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(logging_setup.faulthandler, "enable", lambda *args, **kwargs: None)
    installed_handlers: list[object] = []
    monkeypatch.setattr(
        logging_setup,
        "qInstallMessageHandler",
        lambda handler: installed_handlers.append(handler),
    )

    old_excepthook = sys.excepthook
    old_thread_hook = threading.excepthook
    old_unraisable = sys.unraisablehook
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        application_log, native_log = logging_setup.configure_logging()

        assert application_log == tmp_path / "RoadMatcher" / "logs" / "road-matcher.log"
        assert native_log == tmp_path / "RoadMatcher" / "native-crash.log"
        assert application_log.exists()
        assert native_log.exists()
        assert callable(sys.excepthook)
        assert callable(threading.excepthook)
        assert callable(sys.unraisablehook)
        assert len(installed_handlers) == 1
        assert installed_handlers[0] is logging_setup._QT_HANDLER

        captured: list[tuple[int, str]] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append((record.levelno, record.getMessage()))

        qt_logger = logging.getLogger("Qt")
        capture = Capture()
        qt_logger.addHandler(capture)
        qt_logger.setLevel(logging.DEBUG)
        context = SimpleNamespace(file="view.py", line=7, function="draw")
        handler = logging_setup._QT_HANDLER
        handler(QtMsgType.QtWarningMsg, context, "warning text")
        handler(QtMsgType.QtCriticalMsg, None, "critical text")
        qt_logger.removeHandler(capture)

        assert any(level == logging.WARNING and "view.py:7:draw" in text for level, text in captured)
        assert any(level == logging.ERROR and text == "critical text" for level, text in captured)
    finally:
        sys.excepthook = old_excepthook
        threading.excepthook = old_thread_hook
        sys.unraisablehook = old_unraisable
        for handler in list(root.handlers):
            try:
                handler.close()
            except Exception:
                pass
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
        if logging_setup._LOG_STREAM is not None:
            try:
                logging_setup._LOG_STREAM.close()
            except Exception:
                pass
            logging_setup._LOG_STREAM = None
