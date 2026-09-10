from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import main as main_module


def test_application_icon_path_prefers_png_on_non_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resources = tmp_path / "app" / "resources"
    resources.mkdir(parents=True)
    (resources / "road_matcher.png").write_bytes(b"png")
    (resources / "road_matcher.ico").write_bytes(b"ico")

    monkeypatch.setattr(main_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(main_module, "os", SimpleNamespace(name="posix"))

    assert main_module._application_icon_path() == resources / "road_matcher.png"


def test_application_icon_path_prefers_ico_on_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resources = tmp_path / "app" / "resources"
    resources.mkdir(parents=True)
    (resources / "road_matcher.png").write_bytes(b"png")
    (resources / "road_matcher.ico").write_bytes(b"ico")

    monkeypatch.setattr(main_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(main_module, "os", SimpleNamespace(name="nt"))

    assert main_module._application_icon_path() == resources / "road_matcher.ico"


def test_application_icon_path_falls_back_to_png_when_windows_ico_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resources = tmp_path / "app" / "resources"
    resources.mkdir(parents=True)
    (resources / "road_matcher.png").write_bytes(b"png")

    monkeypatch.setattr(main_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(main_module, "os", SimpleNamespace(name="nt"))

    assert main_module._application_icon_path() == resources / "road_matcher.png"


def test_windows_app_identity_is_noop_outside_windows(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main_module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        main_module,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(
                shell32=SimpleNamespace(
                    SetCurrentProcessExplicitAppUserModelID=lambda value: calls.append(value)
                )
            )
        ),
    )

    main_module._set_windows_app_identity()
    assert calls == []


def test_windows_app_identity_calls_shell_api_on_windows(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        main_module,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(
                shell32=SimpleNamespace(
                    SetCurrentProcessExplicitAppUserModelID=lambda value: calls.append(value)
                )
            )
        ),
    )

    main_module._set_windows_app_identity()

    assert calls == [main_module.WINDOWS_APP_USER_MODEL_ID]
