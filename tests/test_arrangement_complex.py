"""Reconstruction state-machine tests with a modeled overlap API, not real Live."""

import gzip
import hashlib
import sys
from types import SimpleNamespace as NS

import pytest
from test_arrangement_engine import Clip, Slot, execute, preview

pytest_plugins = ["test_arrangement_engine"]


def bounds(engine):
    return sorted((c.start_time, c.end_time) for c in engine.song.tracks[0].arrangement_clips)


@pytest.mark.parametrize(
    "edit_request,expected",
    [
        ({"operation": "trim", "start": 1, "end": 3}, [(1, 3)]),
        ({"operation": "trim", "end": 2}, [(0, 2)]),
        ({"operation": "trim", "start": 2}, [(2, 4)]),
        ({"operation": "move", "destination": 8}, [(8, 12)]),
        ({"operation": "move", "destination": 1}, [(1, 5)]),
        ({"operation": "split", "beat": 2}, [(0, 2), (2, 4)]),
        ({"operation": "split", "beat": 0}, [(0, 4)]),
        ({"operation": "split", "beat": 4}, [(0, 4)]),
    ],
)
def test_native_reconstruction_bounds_and_cleanup(engine, edit_request, expected):
    before = engine._notes(engine.song.tracks[0].arrangement_clips[0])
    result = execute(engine, preview(engine, **edit_request), reconstruction=True)
    assert result["status"] == "edited", result
    assert bounds(engine) == expected
    assert engine.song.undo == ["begin", "end"]
    assert all(not slot.has_clip for slot in engine.song.tracks[0].clip_slots)
    # Content is copied natively, never rewritten through a reduced note schema.
    assert all(engine._notes(c) == before for c in engine.song.tracks[0].arrangement_clips)


def test_reconstruction_requires_separate_authorization(engine):
    result = execute(engine, preview(engine, operation="split", beat=2))
    assert result["code"] == "reconstruction_authorization_required"
    assert bounds(engine) == [(0, 4)]
    assert engine.song.undo == []


def test_move_overlap_and_zero_length_trim_rejected(engine):
    engine.song.tracks[0].arrangement_clips.append(Clip(start=8, end=12))
    assert preview(engine, operation="move", destination=6)["code"] == "destination_overlap"
    assert preview(engine, operation="trim", start=2, end=2)["code"] == "invalid_range"


def test_audio_trim_preserves_file_loop_warp_and_offsets(engine, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    track = engine.song.tracks[0]
    track.has_midi_input = False
    clip = track.arrangement_clips[0]
    clip.is_audio_clip, clip.is_midi_clip = True, False
    clip.start_marker, clip.loop_start, clip.loop_end = 2, 1, 5
    clip.warp_markers = [NS(beat_time=1, sample_time=0.5)]
    result = execute(engine, preview(engine, operation="trim", end=2), reconstruction=True)
    assert result["status"] == "edited", result
    observed = result["observed"]["clips"][0]
    assert observed["file_path"] == "/unchanged/source.wav"
    assert observed["start_marker"] == 2
    assert observed["loop_start"] == 1
    assert observed["warp_markers"] == [{"beat_time": 1, "sample_time": 0.5}]
    assert list(tmp_path.rglob("*.wav")), "Carrier source retained for Undo"


def test_failed_restore_keeps_native_recovery_copy(engine):
    track = engine.song.tracks[0]
    native_copy = track.duplicate_clip_to_arrangement

    def fail_at_destination(clip, start):
        if start == 8:
            raise RuntimeError("injected restore failure")
        return native_copy(clip, start)

    track.duplicate_clip_to_arrangement = fail_at_destination
    result = execute(engine, preview(engine, operation="move", destination=8), reconstruction=True)
    assert result["status"] == "partial_failure"
    assert result["retained_recovery_copies"]
    assert bounds(engine) != []
    assert engine.song.undo == ["begin", "end"]


def test_full_session_uses_and_cleans_an_automatic_scene(engine):
    track = engine.song.tracks[0]
    for slot in track.clip_slots:
        slot.create_clip(4)
    originals = [s.clip for s in track.clip_slots]
    original_scenes = [object(), object()]
    engine.song.scenes = list(original_scenes)

    def create_scene(index):
        engine.song.scenes.append(object())
        track.clip_slots.append(Slot())

    def delete_scene(index):
        engine.song.scenes.pop(index)
        track.clip_slots.pop()

    engine.song.create_scene, engine.song.delete_scene = create_scene, delete_scene
    result = execute(engine, preview(engine, operation="trim", end=2), reconstruction=True)
    assert result["status"] == "edited", result
    assert engine.song.scenes == original_scenes
    assert [s.clip for s in track.clip_slots] == originals


@pytest.fixture
def automation(engine, tmp_path, monkeypatch):
    events = []
    envelope = NS(
        create_event=lambda event: events.append(event),
        insert_step=lambda *args: events.append(args),
    )
    param = NS(
        name="Volume",
        min=0,
        max=1,
        is_quantized=False,
        is_enabled=True,
        value=0.75,
        automation_state=1,
        envelope=envelope,
    )
    engine.song.tracks[0].mixer_device = NS(volume=param)
    live_envelope = NS(EnvelopeEvent=lambda t, v: (t, v))
    monkeypatch.setitem(sys.modules, "Live.Envelope", live_envelope)
    path = tmp_path / "snapshot.als"
    path.write_bytes(gzip.compress(b"<Ableton><LiveSet/></Ableton>"))
    request = {
        "operation": "automation",
        "start": 1,
        "end": 3,
        "parameter_reference": engine._handle(engine.song.tracks[0], param),
        "points": [{"time": 1, "value": 0.2}, {"time": 3, "value": 0.8}],
        "snapshot_path": str(path),
        "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "snapshot_confirmed_current": True,
    }
    return request, events


def test_automation_stamps_existing_track_and_restores_clip(engine, automation):
    request, events = automation
    result = execute(engine, preview(engine, **request), reconstruction=True)
    assert result["status"] == "edited", result
    assert bounds(engine) == [(0, 4)]
    assert events == [(2, 0.8), (0, 0.2)]
    assert len(engine.song.tracks) == 1
    assert all(not s.has_clip for s in engine.song.tracks[0].clip_slots)


def test_automation_parking_avoids_the_entire_destination_range(engine, automation):
    request, _ = automation
    request["end"] = 100
    request["points"][-1]["time"] = 100
    result = execute(engine, preview(engine, **request), reconstruction=True)
    assert result["status"] == "edited", result
    assert bounds(engine) == [(0, 4)]


def test_holding_area_avoids_take_lanes(engine):
    engine.song.tracks[0].take_lanes = [NS(arrangement_clips=[Clip(start=80, end=100)])]
    assert engine._holding(4) > 100


def test_automation_override_is_not_silently_reenabled(engine, automation):
    request, _ = automation
    engine.song.tracks[0].mixer_device.volume.automation_state = 2
    assert preview(engine, **request)["code"] == "automation_overridden"
    assert engine.song.undo == []


def test_step_automation_uses_bounded_segments(engine, automation):
    request, events = automation
    request["interpolation"] = "step"
    result = execute(engine, preview(engine, **request), reconstruction=True)
    assert result["status"] == "edited", result
    assert events == [(0, 2, 0.2)]


def test_stale_snapshot_rejected_before_mutation(engine, automation):
    request, _ = automation
    request["snapshot_sha256"] = "old"
    assert preview(engine, **request)["code"] == "stale_snapshot"
    assert engine.song.undo == []


def test_automation_requires_freshness_confirmation(engine, automation):
    request, _ = automation
    request["snapshot_confirmed_current"] = False
    assert preview(engine, **request)["code"] == "snapshot_required"


def test_curves_and_quantized_parameters_rejected(engine, automation):
    request, _ = automation
    request["interpolation"] = "curve"
    assert preview(engine, **request)["code"] == "unsupported_interpolation"
    request["interpolation"] = "linear"
    engine.song.tracks[0].mixer_device.volume.is_quantized = True
    assert preview(engine, **request)["code"] == "unsupported_parameter"


def test_automation_checks_snapshot_again_at_execution(engine, automation):
    request, _ = automation
    plan = preview(engine, **request)
    from pathlib import Path

    Path(request["snapshot_path"]).write_bytes(b"changed")
    assert execute(engine, plan, reconstruction=True)["code"] == "stale_snapshot"
    assert engine.song.undo == []
