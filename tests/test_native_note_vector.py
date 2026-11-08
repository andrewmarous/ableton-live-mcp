"""Model Live's native-vector-only apply_note_modifications contract."""

import copy

from test_arrangement_engine import execute, preview

pytest_plugins = ["test_arrangement_engine"]


class NativeNotes:
    def __init__(self, notes):
        self.notes = copy.deepcopy(notes)

    def __iter__(self):
        return iter(self.notes)


def test_updates_pass_original_native_vector_and_preserve_other_notes(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    unrelated = copy.deepcopy(clip.notes[0])
    unrelated.note_id = 2
    unrelated.pitch = 67
    clip.notes.append(unrelated)
    vector = NativeNotes(clip.notes)
    clip.get_all_notes_extended = lambda: vector

    def apply(notes):
        if notes is not vector:
            raise TypeError("apply_note_modifications requires original native note vector")
        clip.notes = copy.deepcopy(list(notes))

    clip.apply_note_modifications = apply
    result = execute(
        engine, preview(engine, operation="edit_notes", updates=[{"note_id": 1, "velocity": 81}])
    )
    assert result["status"] == "edited"
    assert clip.notes[0].velocity == 81
    assert vars(clip.notes[1]) == vars(unrelated)
    assert clip.notes[0].probability == 0.7
    assert clip.notes[0].velocity_deviation == 2


def test_combined_update_and_removal_does_not_restore_deleted_note(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    other = copy.deepcopy(clip.notes[0])
    other.note_id = 2
    other.pitch = 67
    clip.notes.append(other)
    vector = NativeNotes(clip.notes)
    clip.get_all_notes_extended = lambda: vector

    def apply(notes):
        if notes is not vector:
            raise TypeError("native vector required")
        clip.notes = copy.deepcopy(list(notes))

    clip.apply_note_modifications = apply
    result = execute(
        engine,
        preview(
            engine, operation="edit_notes", updates=[{"note_id": 1, "velocity": 81}], remove_ids=[2]
        ),
    )
    assert result["status"] == "edited"
    assert [n.note_id for n in clip.notes] == [1]
    assert clip.notes[0].velocity == 81
