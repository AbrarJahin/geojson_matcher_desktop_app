from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.core.pipeline import PipelineConfig, RoadMatchingPipeline


class AnalysisWorker(QObject):
    log = Signal(str)
    stage = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, config: PipelineConfig):
        super().__init__()
        self.config = config

    @Slot()
    def run(self) -> None:
        try:
            pipeline = RoadMatchingPipeline(self.config, logger=self.log.emit)
            pipeline.run_analysis(stage_callback=self.stage.emit)
            self.completed.emit(pipeline)
        except Exception:
            self.failed.emit(traceback.format_exc())


class FinalizationWorker(QObject):
    log = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: RoadMatchingPipeline):
        super().__init__()
        self.pipeline = pipeline

    @Slot()
    def run(self) -> None:
        try:
            previous_logger = self.pipeline._logger
            self.pipeline._logger = self.log.emit
            try:
                result = self.pipeline.finalize()
            finally:
                self.pipeline._logger = previous_logger
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
