"""Regressions based on observed Live 12.4.16 behavior."""

from test_arrangement_engine import execute, module, preview

pytest_plugins = ["test_arrangement_engine"]


class ClampedCarrier:
    is_audio_clip = True
    warping = True
    looping = False
    loop_start = 0.0
    start_marker = 0.0

    @property
    def loop_end(self):
        return self._end

    @loop_end.setter
    def loop_end(self, value):
        self._end = max(0.25, value) if self.looping else value


def test_audio_carrier_does_not_clamp_sub_quarter_beat_length():
    carrier = module.ArrangementEngine._configure_carrier(ClampedCarrier(), 0.1)
    assert carrier.loop_end == 0.1
    assert carrier.looping is False


def test_nonlooped_audio_source_marker_updates_effective_offset(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    clip.is_audio_clip, clip.is_midi_clip, clip.looping = True, False, False
    plan = preview(engine, operation="properties", properties={"start_marker": 0.5})
    result = execute(engine, plan)
    assert result["status"] == "edited"
    assert clip.loop_start == 0.5
    assert result["requested"]["properties"]["loop_start"] == 0.5


def test_audio_loop_mode_change_requires_separate_marker_request(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    clip.is_audio_clip, clip.is_midi_clip, clip.looping = True, False, False
    result = preview(engine, operation="properties", properties={"looping": True, "loop_end": 1})
    assert result["code"] == "mode_change"
    assert engine.song.undo == []


def test_loop_markers_are_written_before_source_markers(engine):
    clip = engine.song.tracks[0].arrangement_clips[0]
    parent = type(clip)

    class ResettingClip(parent):
        def __setattr__(self, name, value):
            if name == "loop_start":
                super().__setattr__("start_marker", 0)
            super().__setattr__(name, value)

    clip.__class__ = ResettingClip
    clip.is_audio_clip, clip.is_midi_clip, clip.looping = True, False, True
    result = execute(
        engine,
        preview(engine, operation="properties", properties={"loop_start": 1, "start_marker": 1}),
    )
    assert result["status"] == "edited"
    assert result["pending_properties"] == []
    assert clip.start_marker == 1


def test_float_rounding_is_not_pending_but_real_difference_is():
    assert module._property_matches(0.30000001192092896, 0.3)
    assert not module._property_matches(0.0, 0.05)
    assert not module._property_matches(False, 0.0)
