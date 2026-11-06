"""Reference safety without a running Live instance."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_path = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/arrangement_refs.py"
_spec = importlib.util.spec_from_file_location("arrangement_refs", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
ArrangementReferences = _module.ArrangementReferences
StaleArrangementReference = _module.StaleArrangementReference


def fixture_objects():
    clip = object()
    track = SimpleNamespace(arrangement_clips=[clip])
    return SimpleNamespace(tracks=[track]), track, clip


def test_reference_survives_unrelated_edits():
    song, track, clip = fixture_objects()
    refs = ArrangementReferences()
    handle = refs.register(track, clip)
    track.arrangement_clips.insert(0, object())
    song.tracks.insert(0, SimpleNamespace(arrangement_clips=[]))
    assert refs.resolve(song, handle) == (track, clip)
    assert refs.register(track, clip) == handle


@pytest.mark.parametrize("change", ["clip", "track", "set", "reconnect"])
def test_stale_references(change):
    song, track, clip = fixture_objects()
    refs = ArrangementReferences()
    handle = refs.register(track, clip)
    if change == "clip":
        track.arrangement_clips.clear()
    elif change == "track":
        song.tracks.clear()
    elif change == "set":
        song = SimpleNamespace(tracks=[])
    else:
        refs = ArrangementReferences()
    with pytest.raises(StaleArrangementReference):
        refs.resolve(song, handle)


def test_unknown_handle_is_rejected():
    song, _, _ = fixture_objects()
    with pytest.raises(StaleArrangementReference):
        ArrangementReferences().resolve(song, "unknown")
