import gzip
from pathlib import Path

import pytest
from test_arrangement_engine import Clip, execute, preview

pytest_plugins = ["test_arrangement_engine"]


def test_missing_recovery_file_blocks_before_mutation(engine):
    Path(engine.recovery_point).unlink()
    result = execute(engine, preview(engine, operation="duplicate", destination=8))
    assert result["code"] == "recovery_required"
    assert engine.song.undo == []


def test_delete_uses_identity_and_returns_remaining_state(engine):
    track = engine.song.tracks[0]
    unrelated = Clip(start=8, end=12)
    track.arrangement_clips.insert(0, unrelated)
    result = execute(engine, preview(engine, operation="delete"))
    assert result["status"] == "edited"
    assert result["observed"]["deleted"]["reference"] == engine.handle
    assert track.arrangement_clips == [unrelated]


def test_note_boolean_ids_rejected(engine):
    assert preview(engine, operation="edit_notes", remove_ids=[True])["code"] == "invalid_notes"


def test_extended_zero_velocity_is_valid(engine):
    result = execute(
        engine, preview(engine, operation="edit_notes", add=[{"pitch": 61, "velocity": 0}])
    )
    assert result["status"] == "edited"
    assert result["observed"]["notes"][-1]["velocity"] == 0


def test_fractional_detune_and_correct_upper_limit(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    clip.is_midi_clip, clip.is_audio_clip = False, True
    result = execute(
        engine, preview(engine, operation="properties", properties={"pitch_fine": 2.5})
    )
    assert result["observed"]["pitch_fine"] == 2.5
    assert (
        preview(engine, operation="properties", properties={"pitch_fine": 50})["code"]
        == "invalid_range"
    )


@pytest.fixture
def warped(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    clip.is_midi_clip, clip.is_audio_clip = False, True
    clip.sample_rate, clip.sample_length = 44100, 44100 * 4
    clip.warp_markers = [
        {"beat_time": 0, "sample_time": 0},
        {"beat_time": 4, "sample_time": 2},
        {"beat_time": 8, "sample_time": 4},
    ]
    clip.add_warp_marker = lambda marker: clip.warp_markers.append(
        {
            "beat_time": marker["beat_time"],
            "sample_time": marker.get("sample_time", marker["beat_time"] / 2),
        }
    )

    def move(beat, distance):
        next(m for m in clip.warp_markers if m["beat_time"] == beat)["beat_time"] += distance

    clip.move_warp_marker = move
    clip.remove_warp_marker = lambda beat: clip.warp_markers.remove(
        next(m for m in clip.warp_markers if m["beat_time"] == beat)
    )
    return clip


def test_dict_warp_markers_are_inspectable(engine, warped):
    observed = engine.command("inspect", {"reference": engine.handle})["clip"]
    assert observed["warp_markers"][1] == {"beat_time": 4, "sample_time": 2}


@pytest.mark.parametrize(
    "edit",
    [
        {"action": "add", "marker": {"beat_time": 2}},
        {"action": "move", "beat_time": 4, "destination_beat": 5},
        {"action": "remove", "beat_time": 4},
    ],
)
def test_native_warp_marker_edits(engine, warped, edit):
    result = execute(engine, preview(engine, operation="warp_marker", **edit))
    assert result["status"] == "edited", result
    assert result["observed"]["file_path"] == "/unchanged/source.wav"


@pytest.mark.parametrize(
    "edit",
    [
        {"action": "add", "marker": {"beat_time": 2, "sample_time": 5}},
        {"action": "move", "beat_time": 4, "destination_beat": 8},
        {"action": "remove", "beat_time": 8},
    ],
)
def test_invalid_warp_grid_rejected_before_mutation(engine, warped, edit):
    result = preview(engine, operation="warp_marker", **edit)
    assert result["status"] == "error"
    assert engine.song.undo == []


def test_track_deletion_and_new_registry_reject_old_handles(engine):
    track = engine.song.tracks.pop()
    assert engine.command("inspect", {"reference": engine.handle})["code"] == "stale_reference"
    engine.song.tracks.append(track)
    other = type(engine)(engine.host)
    assert other.command("inspect", {"reference": engine.handle})["code"] == "stale_reference"


def test_audio_preparation_waits_for_warp_tick_and_can_cancel(engine, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    track = engine.song.tracks[0]
    track.has_midi_input = False
    clip = track.arrangement_clips[0]
    clip.is_midi_clip, clip.is_audio_clip = False, True
    plan = preview(engine, operation="trim", end=2)
    params = {
        "plan": plan["plan"],
        "authorized": True,
        "recovery_point": engine.recovery_point,
        "reconstruction_authorized": True,
    }
    first = engine.command("execute", params)
    assert first["status"] == "prepared"
    assert first["arrangement_changed"] is False
    assert track.arrangement_clips == [clip]
    carrier = engine.prepared[plan["plan"]]["carriers"][0][1]
    carrier.warping = False
    assert engine.command("execute", params)["status"] == "prepared"
    assert track.arrangement_clips == [clip]
    cancelled = engine.command("execute", dict(params, cancel=True))
    assert cancelled["status"] == "cancelled"
    assert all(not slot.has_clip for slot in track.clip_slots)
    assert plan["plan"] not in engine.prepared


def test_cancel_preserves_a_replaced_temporary_slot(engine, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    track = engine.song.tracks[0]
    track.has_midi_input = False
    clip = track.arrangement_clips[0]
    clip.is_midi_clip, clip.is_audio_clip = False, True
    plan = preview(engine, operation="trim", end=2)
    params = {
        "plan": plan["plan"],
        "authorized": True,
        "recovery_point": engine.recovery_point,
        "reconstruction_authorized": True,
    }
    assert engine.command("execute", params)["status"] == "prepared"
    slot = engine.prepared[plan["plan"]]["scratch"][0][0]
    replacement = Clip()
    slot.clip = replacement
    cancelled = engine.command("execute", dict(params, cancel=True))
    assert cancelled["cleanup_warnings"]
    assert slot.clip is replacement


def test_recovery_source_file_is_never_rewritten(engine):
    before = Path(engine.recovery_point).read_bytes()
    execute(engine, preview(engine, operation="move", destination=16), reconstruction=True)
    assert Path(engine.recovery_point).read_bytes() == before
    assert gzip.decompress(before).startswith(b"<Ableton>")
