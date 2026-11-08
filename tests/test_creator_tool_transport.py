import json
from types import SimpleNamespace

import pytest

from ableton_live_mcp.tools import launch_controls, mixer_controls, scene_controls, simpler_controls


@pytest.mark.parametrize(
    "module,name,kwargs",
    [
        (launch_controls, "get_session_launch_settings", {"track_index": 0, "clip_index": 1}),
        (
            launch_controls,
            "set_session_launch_settings",
            {"track_index": 0, "clip_index": 1, "settings": {"legato": True}},
        ),
        (launch_controls, "trigger_session_clip", {"track_index": 0, "clip_index": 1}),
        (scene_controls, "get_scene_musical_settings", {"scene_index": 0}),
        (
            scene_controls,
            "set_scene_musical_settings",
            {"scene_index": 0, "settings": {"tempo": 96}},
        ),
        (simpler_controls, "get_simpler_sample_controls", {"track_index": 0, "device_index": 1}),
        (
            simpler_controls,
            "set_simpler_sample_controls",
            {"track_index": 0, "device_index": 1, "sample_settings": {"start_marker": 400}},
        ),
        (
            simpler_controls,
            "edit_simpler_slice",
            {"track_index": 0, "device_index": 1, "action": "insert", "position": 400},
        ),
        (mixer_controls, "get_mixer_controls", {"target": "master"}),
        (
            mixer_controls,
            "set_mixer_controls",
            {"target": "master", "settings": {"cue_volume": 0.4}},
        ),
    ],
)
def test_tool_forwards_to_matching_bridge_command(monkeypatch, module, name, kwargs):
    calls = []

    def send(command, params):
        calls.append((command, params))
        return {"observed": params}

    monkeypatch.setattr(
        "ableton_live_mcp.tools._creator_controls.get_ableton_connection",
        lambda: SimpleNamespace(send_command=send),
    )
    result = json.loads(getattr(module, name)(None, **kwargs))
    assert calls[0][0] == name
    assert all(value is not None for value in calls[0][1].values())
    for key, value in kwargs.items():
        assert result["observed"][key] == value
