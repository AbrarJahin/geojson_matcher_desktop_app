from pathlib import Path

import pytest

from app.core.pipeline import PipelineConfig, county_name_from_filename


def test_county_name_from_filename() -> None:
    assert county_name_from_filename("Hamilton_County_Boundary.geojson") == "Hamilton"
    assert county_name_from_filename("Marion.json") == "Marion"


def test_config_rejects_same_file(tmp_path: Path) -> None:
    road_file = tmp_path / "roads.geojson"
    road_file.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    config = PipelineConfig(road_file, road_file, tmp_path / "out")
    with pytest.raises(ValueError, match="different"):
        config.validate()
