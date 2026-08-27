from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.core.pipeline import PipelineConfig, RoadMatchingPipeline

LOGGER = logging.getLogger(__name__)


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
        LOGGER.info("Analysis worker started.")
        try:
            pipeline = RoadMatchingPipeline(self.config, logger=self.log.emit)
            pipeline.run_analysis(stage_callback=self.stage.emit)
            # The worker is deleted after its thread exits. Do not return a
            # pipeline that retains a bound Signal.emit method from this worker.
            pipeline.set_logger(None)
            self.completed.emit(pipeline)
            LOGGER.info("Analysis worker completed.")
        except Exception:
            traceback_text = traceback.format_exc()
            LOGGER.error("Analysis worker failed:\n%s", traceback_text)
            self.failed.emit(traceback_text)


class FinalizationWorker(QObject):
    log = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: RoadMatchingPipeline):
        super().__init__()
        self.pipeline = pipeline

    @Slot()
    def run(self) -> None:
        LOGGER.info("Finalization worker started.")
        try:
            previous_logger = self.pipeline._logger
            self.pipeline.set_logger(self.log.emit)
            try:
                result = self.pipeline.finalize()
            finally:
                self.pipeline.set_logger(previous_logger)
            self.completed.emit(result)
            LOGGER.info("Finalization worker completed.")
        except Exception:
            traceback_text = traceback.format_exc()
            LOGGER.error("Finalization worker failed:\n%s", traceback_text)
            self.failed.emit(traceback_text)


class JunctionRoundWorker(QObject):
    log = Signal(str)
    stage = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: RoadMatchingPipeline):
        super().__init__()
        self.pipeline = pipeline

    @Slot()
    def run(self) -> None:
        LOGGER.info("Junction-round worker started.")
        try:
            previous_logger = self.pipeline._logger
            self.pipeline.set_logger(self.log.emit)
            try:
                result = self.pipeline.complete_junction_round(
                    stage_callback=self.stage.emit
                )
            finally:
                self.pipeline.set_logger(previous_logger)
            self.completed.emit(result)
            LOGGER.info("Junction-round worker completed.")
        except Exception:
            traceback_text = traceback.format_exc()
            LOGGER.error("Junction-round worker failed:\n%s", traceback_text)
            self.failed.emit(traceback_text)
