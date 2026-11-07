"""Native API doubles only; actual Live behavior remains an acceptance requirement."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/creator_controls.py"
spec = importlib.util.spec_from_file_location("creator_controls", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def parameter(name, value=0.5, low=0.0, high=1.0):
    return NS(
        name=name,
        original_name=name,
        value=value,
        min=low,
        max=high,
        is_enabled=True,
        is_quantized=False,
    )


class Sample:
    length, sample_rate, file_path = 48000, 48000, "/unchanged/source.wav"
    slicing_style, slicing_sensitivity = 3, 0.5
    slicing_beat_division, slicing_region_count = 4, 8

    def __init__(self):
        self._start, self._end = 0, 48000
        self.slices = [0, 12000]

    @property
    def start_marker(self):
        return self._start

    @start_marker.setter
    def start_marker(self, value):
        assert value < self._end
        self._start = value

    @property
    def end_marker(self):
        return self._end

    @end_marker.setter
    def end_marker(self, value):
        assert value > self._start
        self._end = value

    def insert_slice(self, position):
        self.slices = sorted([*self.slices, position])

    def remove_slice(self, position):
        self.slices.remove(position)

    def move_slice(self, source, destination):
        self.remove_slice(source)
        self.insert_slice(destination)


@pytest.fixture
def setup():
    undo, triggers = [], []
    clip = NS(
        is_session_clip=True,
        launch_mode=0,
        launch_quantization=0,
        legato=False,
        velocity_amount=0.0,
        is_recording=False,
        is_playing=False,
        is_triggered=False,
        set_fire_button_state=lambda state: triggers.append(state),
    )
    slot = NS(fire=lambda **kw: triggers.append(kw))
    device = NS(
        class_name="OriginalSimpler",
        multi_sample_mode=False,
        sample=Sample(),
        playback_mode=0,
        slicing_playback_mode=0,
        pad_slicing=False,
        retrigger=True,
        voices=8,
        parameters=[parameter("Device On"), parameter("Loop On")],
    )
    mixer = NS(
        volume=parameter("Volume"),
        panning=parameter("Pan", 0, -1, 1),
        left_split_stereo=parameter("Left", -1, -1, 1),
        right_split_stereo=parameter("Right", 1, -1, 1),
        panning_mode=0,
        sends=[parameter("Send A")],
    )
    track = NS(devices=[device], clip_slots=[slot], mixer_device=mixer, mute=False, solo=False)
    master = NS(mixer_device=NS(**vars(mixer), cue_volume=parameter("Cue")))
    scene = NS(
        tempo=120.0,
        tempo_enabled=False,
        time_signature_numerator=4,
        time_signature_denominator=4,
        time_signature_enabled=False,
    )
    song = NS(
        tracks=[track],
        return_tracks=[track],
        master_track=master,
        scenes=[scene],
        begin_undo_step=lambda: undo.append("begin"),
        end_undo_step=lambda: undo.append("end"),
    )
    host = NS(
        _song=song,
        _get_track=lambda i: song.tracks[i],
        _get_clip=lambda t, c: [clip][c] if song.tracks[t] else None,
        _get_scene=lambda i: song.scenes[i],
        _get_device=lambda t, d: song.tracks[t].devices[d],
        _resolve_track=lambda i, kind: (
            master
            if kind == "master"
            else (song.return_tracks if kind == "return" else song.tracks)[i]
        ),
    )
    return module.CreatorControls(host), clip, device, track, scene, undo, triggers


def call(setup, method, **kwargs):
    return setup[0].dispatch(
        method, {"track_index": 0, "clip_index": 0, "device_index": 0, "scene_index": 0, **kwargs}
    )


@pytest.mark.parametrize("action", ["get_launch", "get_scene", "get_simpler", "get_mixer"])
def test_inspection_never_mutates_or_launches(setup, action):
    assert "status" not in call(setup, action)
    assert setup[-2:] == ([], [])


def test_launch_settings_and_explicit_trigger(setup):
    result = call(
        setup,
        "set_launch",
        settings={
            "legato": True,
            "launch_mode": 1,
            "launch_quantization": 12,
            "velocity_amount": 0.7,
        },
    )
    assert result["observed"]["values"]["legato"] is True
    assert result["unconfirmed"] == []
    assert setup[-1] == []
    assert setup[-2] == ["begin", "end"]
    assert call(setup, "trigger", launch_quantization=8)["status"] == "triggered"
    call(setup, "trigger", action="press")
    call(setup, "trigger", action="release")
    assert setup[-1] == [{"launch_quantization": 8}, True, False]


@pytest.mark.parametrize(
    "settings",
    [
        {"launch_mode": 4},
        {"legato": 1},
        {"launch_quantization": -1},
        {"velocity_amount": float("nan")},
        {"velocity_amount": float("inf")},
        {"launch_mode": True},
        {"legato": True, "unknown": 3},
    ],
)
def test_launch_invalid_request_has_no_partial_writes(setup, settings):
    assert call(setup, "set_launch", settings=settings)["status"] == "error"
    assert setup[1].legato is False
    assert setup[-2] == []


def test_unsupported_property_is_reported_and_rejected(setup):
    del setup[1].velocity_amount
    assert "velocity_amount" in call(setup, "get_launch")["unsupported"]
    result = call(setup, "set_launch", settings={"legato": True, "velocity_amount": 0.2})
    assert result["code"] == "unsupported"
    assert setup[1].legato is False
    assert setup[-2] == []


def test_trigger_rejects_invalid_override_and_recording(setup):
    assert call(setup, "trigger", action="press", launch_quantization=1)["status"] == "error"
    setup[1].is_recording = True
    assert call(setup, "trigger")["code"] == "recording"
    assert setup[-1] == []


def test_scene_settings_do_not_launch(setup):
    result = call(
        setup,
        "set_scene",
        settings={
            "tempo": 96.0,
            "tempo_enabled": True,
            "time_signature_numerator": 7,
            "time_signature_denominator": 8,
            "time_signature_enabled": False,
        },
    )
    assert result["status"] == "updated"
    assert setup[4].tempo == 96
    assert setup[4].time_signature_enabled is False
    assert setup[-1] == []


def test_scene_invalid_denominator_prevents_all_changes(setup):
    assert (
        call(setup, "set_scene", settings={"tempo": 96, "time_signature_denominator": 3})["code"]
        == "invalid_value"
    )
    assert setup[4].tempo == 120
    assert setup[-2] == []


def test_simpler_region_uses_frames_and_safe_assignment_order(setup):
    sample = setup[2].sample
    sample.end_marker = 10000
    result = call(
        setup, "set_simpler", sample_settings={"start_marker": 20000, "end_marker": 40000}
    )
    assert result["status"] == "updated"
    assert result["observed"]["marker_units"] == "sample_frames"
    assert (sample.start_marker, sample.end_marker) == (20000, 40000)
    assert sample.file_path == "/unchanged/source.wav"


@pytest.mark.parametrize(
    "settings",
    [{"start_marker": 48000}, {"end_marker": 48001}, {"start_marker": 0.5}, {"end_marker": -1}],
)
def test_invalid_sample_regions_do_not_mutate(setup, settings):
    assert call(setup, "set_simpler", sample_settings=settings)["status"] == "error"
    assert setup[2].sample.start_marker == 0
    assert setup[2].sample.end_marker == 48000
    assert setup[-2] == []


def test_simpler_region_can_move_back_before_its_previous_start(setup):
    sample = setup[2].sample
    sample.start_marker = 30000
    result = call(setup, "set_simpler", sample_settings={"end_marker": 20000, "start_marker": 1000})
    assert result["status"] == "updated"
    assert (sample.start_marker, sample.end_marker) == (1000, 20000)


def test_readback_reports_deferred_or_unconfirmed_values(setup):
    class DeferredParameter:
        name, min, max, is_enabled, is_quantized = "Volume", 0.0, 1.0, True, False

        @property
        def value(self):
            return 0.5

        @value.setter
        def value(self, requested):
            pass

    setup[3].mixer_device.volume = DeferredParameter()
    result = call(setup, "set_mixer", settings={"volume": 0.8})
    assert result["unconfirmed"] == [{"setting": "volume", "requested": 0.8, "observed": 0.5}]


def test_simpler_loop_parameter_validation_and_localized_name(setup):
    loop = setup[2].parameters[1]
    loop.name = "Boucle"
    assert len(call(setup, "get_simpler")["loop_parameters"]) == 1
    assert call(setup, "set_simpler", loop_parameters={"1": 0.8})["status"] == "updated"
    assert loop.value == 0.8
    assert call(setup, "set_simpler", loop_parameters={"0": 0.8})["code"] == "unsupported"
    assert call(setup, "set_simpler", loop_parameters={"1": 1.1})["code"] == "invalid_value"


def test_simpler_slicing_and_manual_slice_edits(setup):
    assert call(setup, "edit_slice", action="insert", position=24000)["code"] == "wrong_mode"
    call(setup, "set_simpler", device_settings={"playback_mode": 2})
    result = call(
        setup,
        "set_simpler",
        sample_settings={
            "slicing_style": 3,
            "slicing_sensitivity": 0.8,
            "slicing_beat_division": 6,
            "slicing_region_count": 16,
        },
        device_settings={"slicing_playback_mode": 2, "pad_slicing": True},
    )
    assert result["status"] == "updated"
    assert call(setup, "edit_slice", action="insert", position=24000)["status"] == "updated"
    assert (
        call(setup, "edit_slice", action="move", position=24000, destination=30000)["status"]
        == "updated"
    )
    assert call(setup, "edit_slice", action="remove", position=30000)["status"] == "updated"
    assert setup[2].sample.slices == [0, 12000]
    assert call(setup, "edit_slice", action="remove", position=0)["code"] == "protected_slice"
    assert (
        call(setup, "edit_slice", action="move", position=9000, destination=8000)["code"]
        == "stale_slice"
    )
    assert call(setup, "edit_slice", action="insert", position=12000)["code"] == "slice_collision"


@pytest.mark.parametrize("kind", ["wrong_device", "empty", "multisample", "bad_path"])
def test_simpler_unsupported_targets(setup, kind):
    device = setup[2]
    kwargs = {}
    if kind == "wrong_device":
        device.class_name = "Operator"
    elif kind == "empty":
        device.sample = None
    elif kind == "multisample":
        device.multi_sample_mode = True
    else:
        kwargs["chain_path"] = [[0, 0]]
    assert call(setup, "get_simpler", **kwargs)["status"] == "error"
    assert setup[-2] == []


def test_frozen_simpler_track_is_inspectable_but_not_editable(setup):
    setup[3].is_frozen = True
    assert "sample" in call(setup, "get_simpler")
    assert call(setup, "set_simpler", device_settings={"voices": 4})["code"] == "frozen_track"
    assert call(setup, "edit_slice", action="insert", position=24000)["code"] == "frozen_track"
    assert setup[-2] == []


def test_nested_simpler_access(setup):
    host = setup[0].host
    root = NS(chains=[NS(devices=[setup[2]])])
    host._get_device = lambda t, d: root
    assert "sample" in call(setup, "get_simpler", chain_path=[[0, 0]])


def test_master_cue_and_return_controls(setup):
    assert (
        call(setup, "set_mixer", target="master", settings={"cue_volume": 0.6})["status"]
        == "updated"
    )
    result = call(
        setup,
        "set_mixer",
        target="return",
        target_index=0,
        settings={"volume": 0.7, "panning": -0.25, "mute": True, "solo": True, "sends": {"0": 0.3}},
    )
    assert result["status"] == "updated"
    assert setup[3].mixer_device.volume.value == 0.7
    assert setup[3].mute is True
    assert call(setup, "set_mixer", settings={"cue_volume": 0.5})["code"] == "invalid_target"


def test_split_pan_requires_separate_mode_change(setup):
    assert (
        call(setup, "set_mixer", settings={"panning_mode": 1, "left_split_stereo": -0.5})["code"]
        == "wrong_mode"
    )
    assert setup[-2] == []
    call(setup, "set_mixer", settings={"panning_mode": 1})
    result = call(
        setup, "set_mixer", settings={"left_split_stereo": -0.5, "right_split_stereo": 0.25}
    )
    assert result["observed"]["parameters"]["left_split_stereo"]["value"] == -0.5


def test_mixer_validates_all_parameters_before_writes(setup):
    assert (
        call(setup, "set_mixer", settings={"volume": 0.8, "panning": 2})["code"] == "invalid_value"
    )
    assert setup[3].mixer_device.volume.value == 0.5
    assert setup[-2] == []
    setup[3].mixer_device.volume.is_enabled = False
    assert call(setup, "set_mixer", settings={"volume": 0.2})["code"] == "disabled_parameter"


def test_native_failure_reports_completed_changes_and_undo_group(setup):
    class FailingParameter:
        value, name, min, max, is_enabled, is_quantized = 0.0, "Pan", -1.0, 1.0, True, False

        def __setattr__(self, name, value):
            raise RuntimeError("Native write failed")

    setup[3].mixer_device.panning = FailingParameter()
    result = call(setup, "set_mixer", settings={"volume": 0.8, "panning": 0.25})
    assert result["status"] == "partial_failure"
    assert result["completed"] == ["volume"]
    assert result["observed"]["parameters"]["volume"]["value"] == 0.8
    assert setup[-2] == ["begin", "end"]
