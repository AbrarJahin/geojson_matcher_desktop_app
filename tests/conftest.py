from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    # PySide keeps the QApplication wrapper alive until interpreter shutdown.
    # Explicitly destroy it so the complete test command exits reliably after
    # GUI stress tests and network-request cancellation tests.
    try:
        from PySide6.QtWidgets import QApplication
        import shiboken6

        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        app.processEvents()
        app.quit()
        shiboken6.delete(app)
    except Exception:
        # Test result reporting must not be replaced by cleanup diagnostics.
        pass
