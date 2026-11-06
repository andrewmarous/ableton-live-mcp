"""Live-independent contract tests. These do not replace Live acceptance."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/arrangement_engine.py"
spec = importlib.util.spec_from_file_location("arrangement_engine", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Clip:
    def __init__(self, midi=True, start=0, end=4):
        self.name = "clip"
        self.is_midi_clip, self.is_audio_clip = midi, not midi
        self.start_time, self.end_time = start, end
        self.start_marker, self.end_marker = 0, 4
        self.loop_start, self.loop_end, self.looping = 0, 4, True
        self.gain, self.pitch_coarse, self.pitch_fine = 0.75, 0, 0
        self.warping, self.warp_mode = True, 0
        self.available_warp_modes = [0, 1]
        self.warp_markers, self.file_path = [], "/unchanged/source.wav"
        self.notes = [
            NS(
                note_id=1,
                pitch=60,
                start_time=0,
                duration=1,
                velocity=90,
                mute=False,
                probability=0.7,
                velocity_deviation=2,
                release_velocity=64,
            )
        ]
        self.next_id = 2

    def get_all_notes_extended(self):
        return copy.deepcopy(self.notes)

    def remove_notes_by_id(self, ids):
        self.notes = [n for n in self.notes if n.note_id not in ids]

    def add_new_notes(self, notes):
        for note in notes:
            note.note_id = self.next_id
            self.next_id += 1
            self.notes.append(note)

    def apply_note_modifications(self, notes):
        by_id = {n.note_id: n for n in notes}
        self.notes = [by_id.get(n.note_id, n) for n in self.notes]


class Slot:
    def __init__(self):
        self.clip = None

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip(end=length)
        self.clip.is_session_clip = True
        self.clip.loop_end = length
        self.clip.create_automation_envelope = lambda param: param.envelope

    def create_audio_clip(self, path):
        self.create_clip(4)
        self.clip.is_audio_clip, self.clip.is_midi_clip = True, False
        self.clip.file_path = path

    def delete_clip(self):
        self.clip = None


class Track:
    def __init__(self, clips):
        self.name, self.is_frozen, self.arrangement_clips = "track", False, clips
        self.has_audio_output, self.has_midi_input = True, True
        self.clip_slots = [Slot(), Slot()]

    def duplicate_clip_to_arrangement(self, clip, start):
        result = copy.deepcopy(clip)
        session = getattr(clip, "is_session_clip", False)
        length = clip.loop_end - clip.loop_start if session else clip.end_time - clip.start_time
        result.start_time, result.end_time = start, start + length
        result.is_session_clip = False
        for original in list(self.arrangement_clips):
            if original.start_time >= result.end_time or original.end_time <= start:
                continue
            assert session, "Native Arrangement copies must not target overlaps"
            if start <= original.start_time and result.end_time >= original.end_time:
                self.arrangement_clips.remove(original)
            elif start > original.start_time:
                original.end_time = start
            else:
                original.start_time = result.end_time
        self.arrangement_clips.append(result)
        return result

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(
        sys.modules, "Live", NS(Clip=NS(MidiNoteSpecification=lambda **kw: NS(**kw)))
    )
    clip = Clip()
    track = Track([clip])
    song = NS(tracks=[track], is_playing=False, record_mode=False, undo=[])
    song.begin_undo_step = lambda: song.undo.append("begin")
    song.end_undo_step = lambda: song.undo.append("end")
    app = NS(
        get_major_version=lambda: 12, get_minor_version=lambda: 4, get_bugfix_version=lambda: 6
    )
    host = NS(_song=song, application=lambda: app)
    result = module.ArrangementEngine(host)
    result.handle = result._handle(track, clip)
    recovery = tmp_path / "recovery.als"
    import gzip

    recovery.write_bytes(gzip.compress(b"<Ableton><LiveSet/></Ableton>"))
    result.recovery_point = str(recovery)
    return result


def preview(engine, **request):
    return engine.command("preview", dict(reference=engine.handle, **request))


def execute(engine, plan, reconstruction=False):
    params = {
        "plan": plan["plan"],
        "authorized": True,
        "recovery_point": engine.recovery_point,
        "reconstruction_authorized": reconstruction,
    }
    for _ in range(3):
        result = engine.command("execute", params)
        if result.get("status") != "prepared":
            return result
    return result


def test_inspection_is_read_only_and_complete(engine):
    result = engine.command("inspect", {})
    clip = result["tracks"][0]["clips"][0]
    assert clip["notes"][0]["note_id"] == 1
    assert clip["notes"][0]["release_velocity"] == 64
    assert clip["source_marker_unit"] == "beats"
    assert engine.song.undo == []


def test_reference_survives_list_reordering(engine):
    track = engine.song.tracks[0]
    track.arrangement_clips.insert(0, Clip(start=12, end=16))
    assert engine._resolve(engine.handle)[1].start_time == 0


def test_deleted_target_rejected(engine):
    engine.song.tracks[0].arrangement_clips.clear()
    assert preview(engine, operation="duplicate", destination=8)["code"] == "stale_reference"


def test_duplicate_native_preserves_extended_state(engine):
    plan = preview(engine, operation="duplicate", destination=8)
    assert len(engine.song.tracks[0].arrangement_clips) == 1
    result = execute(engine, plan)
    assert result["status"] == "edited"
    assert result["observed"]["start"] == 8
    assert result["observed"]["notes"][0]["probability"] == 0.7
    assert engine.song.undo == ["begin", "end"]


@pytest.mark.parametrize("destination", [-1, float("nan"), float("inf"), 1576800])
def test_invalid_destinations_rejected(engine, destination):
    assert (
        preview(engine, operation="duplicate", destination=destination)["code"] == "invalid_range"
    )


def test_overlap_rejected(engine):
    assert preview(engine, operation="duplicate", destination=2)["code"] == "destination_overlap"


def test_touching_boundary_is_allowed(engine):
    assert preview(engine, operation="duplicate", destination=4)["status"] == "preview"


def test_cross_track_rejected(engine):
    result = preview(
        engine, operation="duplicate", destination=8, destination_track_reference="other"
    )
    assert result["code"] == "cross_track_unsupported"


def test_unauthorized_execute_does_not_consume_plan(engine):
    plan = preview(engine, operation="duplicate", destination=8)
    result = engine.command("execute", {"plan": plan["plan"]})
    assert result["code"] == "authorization_required"
    assert execute(engine, plan)["status"] == "edited"


def test_stale_and_reused_plans(engine):
    plan = preview(engine, operation="duplicate", destination=8)
    engine.song.tracks[0].arrangement_clips[0].name = "changed"
    assert execute(engine, plan)["code"] == "stale_plan"
    assert execute(engine, plan)["code"] == "stale_plan"


def test_targeted_notes_preserve_unrelated_fields(engine):
    plan = preview(
        engine,
        operation="edit_notes",
        updates=[{"note_id": 1, "velocity": 110}],
        add=[{"pitch": 62, "start_time": 2}],
    )
    result = execute(engine, plan)
    assert result["status"] == "edited"
    notes = result["observed"]["notes"]
    assert len(notes) == 2
    assert notes[0]["velocity"] == 110
    assert notes[0]["probability"] == 0.7
    assert notes[0]["release_velocity"] == 64


def test_remove_by_id(engine):
    result = execute(engine, preview(engine, operation="edit_notes", remove_ids=[1]))
    assert result["observed"]["notes"] == []


def test_full_replacement_is_explicit(engine):
    result = execute(engine, preview(engine, operation="replace_notes", notes=[{"pitch": 70}]))
    assert [n["pitch"] for n in result["observed"]["notes"]] == [70]


def test_note_overlap_rejected_before_mutation(engine):
    result = preview(engine, operation="edit_notes", add=[{"pitch": 60, "start_time": 0.5}])
    assert result["code"] == "note_overlap"
    assert engine.song.undo == []


def test_stale_note_ids_rejected(engine):
    result = preview(engine, operation="edit_notes", updates=[{"note_id": 999, "velocity": 70}])
    assert result["code"] == "invalid_notes"


def test_audio_properties_and_source_units(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    clip.is_midi_clip, clip.is_audio_clip, clip.warping = False, True, False
    result = execute(engine, preview(engine, operation="properties", properties={"gain": 0.5}))
    assert result["observed"]["gain"] == 0.5
    assert result["observed"]["file_path"] == "/unchanged/source.wav"
    assert result["observed"]["source_marker_unit"] == "seconds"


def test_source_marker_not_reported_as_timeline_trim(engine):
    result = execute(
        engine, preview(engine, operation="properties", properties={"start_marker": 1})
    )
    assert result["observed"]["start_marker"] == 1
    assert result["observed"]["start"] == 0


def test_invalid_properties_rejected_before_mutation(engine):
    result = preview(engine, operation="properties", properties={"start_marker": 5})
    assert result["code"] == "invalid_range"
    assert engine.song.undo == []


def test_version_gate(engine):
    engine.host.application().get_minor_version = lambda: 3
    assert engine.command("inspect", {})["code"] == "unsupported_version"


def test_frozen_and_playing_gate(engine):
    engine.song.tracks[0].is_frozen = True
    assert preview(engine, operation="duplicate", destination=8)["code"] == "frozen_track"
    engine.song.tracks[0].is_frozen = False
    engine.song.is_playing = True
    assert preview(engine, operation="duplicate", destination=8)["code"] == "transport_running"


def test_partial_failure_closes_undo_and_reports_recovery(engine):
    def broken(clip, destination):
        raise RuntimeError("Live refused")

    engine.song.tracks[0].duplicate_clip_to_arrangement = broken
    result = execute(engine, preview(engine, operation="duplicate", destination=8))
    assert result["status"] == "partial_failure"
    assert "reopen" in result["recovery"]
    assert engine.song.undo == ["begin", "end"]
