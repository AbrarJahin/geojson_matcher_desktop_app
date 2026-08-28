from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

import app.workers.tasks as tasks_module
from app.workers.tasks import AnalysisWorker, FinalizationWorker, JunctionRoundWorker


def _app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_analysis_worker_emits_stage_completed_and_detaches_logger(monkeypatch) -> None:
    _app()
    created: list[object] = []

    class FakePipeline:
        def __init__(self, config, logger):  # type: ignore[no-untyped-def]
            self.config = config
            self.logger = logger
            self.detached = False
            created.append(self)

        def run_analysis(self, stage_callback):  # type: ignore[no-untyped-def]
            self.logger("working")
            stage_callback(2, 5, "stage.py")

        def set_logger(self, logger):  # type: ignore[no-untyped-def]
            self.detached = logger is None

    monkeypatch.setattr(tasks_module, "RoadMatchingPipeline", FakePipeline)
    worker = AnalysisWorker(SimpleNamespace(name="config"))  # type: ignore[arg-type]
    logs: list[str] = []
    stages: list[tuple[int, int, str]] = []
    completed: list[object] = []
    failed: list[str] = []
    worker.log.connect(logs.append)
    worker.stage.connect(lambda a, b, c: stages.append((a, b, c)))
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert logs == ["working"]
    assert stages == [(2, 5, "stage.py")]
    assert completed == created
    assert failed == []
    assert created[0].detached is True  # type: ignore[attr-defined]


def test_analysis_worker_emits_traceback_on_failure(monkeypatch) -> None:
    _app()

    class BrokenPipeline:
        def __init__(self, config, logger):  # type: ignore[no-untyped-def]
            raise RuntimeError("analysis boom")

    monkeypatch.setattr(tasks_module, "RoadMatchingPipeline", BrokenPipeline)
    worker = AnalysisWorker(SimpleNamespace())  # type: ignore[arg-type]
    failures: list[str] = []
    completed: list[object] = []
    worker.failed.connect(failures.append)
    worker.completed.connect(completed.append)

    worker.run()

    assert completed == []
    assert len(failures) == 1
    assert "RuntimeError: analysis boom" in failures[0]


class _FakePipeline:
    def __init__(self) -> None:
        self._logger = "original"
        self.logger_history: list[object] = []
        self.finalize_result = object()
        self.junction_result = object()
        self.fail_finalize = False
        self.fail_junction = False

    def set_logger(self, logger):  # type: ignore[no-untyped-def]
        self._logger = logger
        self.logger_history.append(logger)

    def finalize(self):  # type: ignore[no-untyped-def]
        if self.fail_finalize:
            raise RuntimeError("finalize boom")
        if callable(self._logger):
            self._logger("finalizing")
        return self.finalize_result

    def complete_junction_round(self, stage_callback):  # type: ignore[no-untyped-def]
        if self.fail_junction:
            raise RuntimeError("junction boom")
        if callable(self._logger):
            self._logger("junction")
        stage_callback(1, 3, "round")
        return self.junction_result


def test_finalization_worker_restores_pipeline_logger_on_success() -> None:
    _app()
    pipeline = _FakePipeline()
    worker = FinalizationWorker(pipeline)  # type: ignore[arg-type]
    logs: list[str] = []
    completed: list[object] = []
    failures: list[str] = []
    worker.log.connect(logs.append)
    worker.completed.connect(completed.append)
    worker.failed.connect(failures.append)

    worker.run()

    assert logs == ["finalizing"]
    assert completed == [pipeline.finalize_result]
    assert failures == []
    assert pipeline._logger == "original"


def test_finalization_worker_restores_logger_and_emits_failure() -> None:
    _app()
    pipeline = _FakePipeline()
    pipeline.fail_finalize = True
    worker = FinalizationWorker(pipeline)  # type: ignore[arg-type]
    failures: list[str] = []
    worker.failed.connect(failures.append)

    worker.run()

    assert pipeline._logger == "original"
    assert len(failures) == 1
    assert "RuntimeError: finalize boom" in failures[0]


def test_junction_round_worker_emits_stage_completed_and_restores_logger() -> None:
    _app()
    pipeline = _FakePipeline()
    worker = JunctionRoundWorker(pipeline)  # type: ignore[arg-type]
    logs: list[str] = []
    stages: list[tuple[int, int, str]] = []
    completed: list[object] = []
    worker.log.connect(logs.append)
    worker.stage.connect(lambda a, b, c: stages.append((a, b, c)))
    worker.completed.connect(completed.append)

    worker.run()

    assert logs == ["junction"]
    assert stages == [(1, 3, "round")]
    assert completed == [pipeline.junction_result]
    assert pipeline._logger == "original"


def test_junction_round_worker_restores_logger_and_emits_failure() -> None:
    _app()
    pipeline = _FakePipeline()
    pipeline.fail_junction = True
    worker = JunctionRoundWorker(pipeline)  # type: ignore[arg-type]
    failures: list[str] = []
    worker.failed.connect(failures.append)

    worker.run()

    assert pipeline._logger == "original"
    assert len(failures) == 1
    assert "RuntimeError: junction boom" in failures[0]
