import hashlib
import importlib.util
import json
import wave
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "scripts/create_arrangement_fixture.py"
spec = importlib.util.spec_from_file_location("create_arrangement_fixture", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_fixture_assets_are_reproducible_and_do_not_connect_to_live(tmp_path):
    first = module.create_fixture(tmp_path / "one")
    second = module.create_fixture(tmp_path / "two")
    assert len(first["audio_tracks"]) == 4
    for a, b in zip(first["audio_tracks"], second["audio_tracks"]):
        assert a["sha256"] == b["sha256"]
        assert hashlib.sha256(Path(a["path"]).read_bytes()).hexdigest() == a["sha256"]
        with wave.open(a["path"]) as stream:
            assert stream.getnframes() / stream.getframerate() == 32
        assert a["arrangement_positions"][-1] == 128
    assert json.loads((tmp_path / "one/fixture.json").read_text())["tempo"] == 120


def test_fixture_generator_refuses_to_overwrite(tmp_path):
    marker = tmp_path / "personal.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        module.create_fixture(tmp_path)
    assert marker.read_text() == "keep"
