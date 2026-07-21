from app.core.pipeline import RoadMatchingPipeline


def test_deleted_qt_logger_does_not_break_pipeline_work() -> None:
    pipeline = object.__new__(RoadMatchingPipeline)

    def deleted_signal(_: str) -> None:
        raise RuntimeError("Signal source has been deleted")

    pipeline._logger = deleted_signal
    pipeline.log("Saved manual decision YES")
    pipeline.log("A later message must also remain harmless")
