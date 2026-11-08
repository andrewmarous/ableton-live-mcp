"""Transport regressions from native Live 12.4 acceptance tests."""

import asyncio
import json

import pytest

from ableton_live_mcp.tools import session


class NativeTransport:
    """Stopped seeks do not move Live's remembered playback start point."""

    def __init__(self, playing=False):
        self.playing = playing
        self.position = 12.0
        self.calls = []
        self.ignore_seek = False

    def send_command(self, command, params=None):
        self.calls.append((command, params))
        if command == "get_session_info":
            return {
                "is_playing": self.playing,
                "current_song_time": self.position,
                "tempo": 120.0,
                "loop": False,
            }
        if command == "continue_playing":
            self.playing = True
        elif command == "start_playback":
            self.playing = True
            self.position = 0.0
        elif command == "set_current_song_time":
            assert self.playing, "Seek must happen after playback is observed running"
            if not self.ignore_seek:
                self.position = params["time"]
        elif command == "stop_playback":
            self.playing = False
        else:
            raise AssertionError(command)
        # Native mutation responses can contain stale pre-command state.
        return {"playing": False, "current_song_time": -1}


@pytest.fixture
def transport(monkeypatch):
    conn = NativeTransport()
    monkeypatch.setattr(session, "get_ableton_connection", lambda: conn)
    return conn


@pytest.mark.parametrize("beat", [0, 40, 123.5])
@pytest.mark.parametrize("already_playing", [False, True])
def test_explicit_play_seeks_after_start_and_returns_observed_state(
    transport, beat, already_playing
):
    transport.playing = already_playing
    result = json.loads(session.play(None, start_beat=beat))
    assert result["is_playing"] is True
    assert result["current_song_time"] == beat
    assert result["requested_start_beat"] == beat
    assert ("set_current_song_time", {"time": beat}) in transport.calls
    assert ("continue_playing", None) not in transport.calls or not already_playing


@pytest.mark.parametrize("already_playing", [False, True])
def test_omitted_beat_resumes_without_seeking(transport, already_playing):
    transport.playing = already_playing
    result = json.loads(session.play(None))
    assert result["current_song_time"] == 12
    assert result["is_playing"] is True
    assert not any(command == "set_current_song_time" for command, _ in transport.calls)
    assert sum(command == "continue_playing" for command, _ in transport.calls) == int(
        not already_playing
    )


@pytest.mark.parametrize("beat", [-1, float("nan"), float("inf"), True])
def test_invalid_beat_fails_before_transport_changes(transport, beat):
    with pytest.raises(ValueError):
        session.play(None, start_beat=beat)
    assert transport.calls == []


def test_stop_returns_observed_state(transport):
    transport.playing = True
    result = json.loads(session.stop(None))
    assert result["is_playing"] is False
    assert result["current_song_time"] == 12


def test_failed_seek_is_not_reported_as_success(transport, monkeypatch):
    transport.ignore_seek = True
    monkeypatch.setattr(session, "TRANSPORT_TIMEOUT", 0.01)
    with pytest.raises(RuntimeError, match="verify"):
        session.play(None, start_beat=40)


def test_only_unified_transport_tools_are_exposed():
    from ableton_live_mcp.app import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert {"play", "stop"} <= tools.keys()
    assert not {"start_playback", "continue_playing", "stop_playback"} & tools.keys()
    beat = tools["play"].inputSchema["properties"]["start_beat"]
    assert beat["default"] is None


def test_play_cannot_be_batched(transport):
    with pytest.raises(ValueError, match="separate"):
        session.batch_commands(None, [{"type": "play", "params": {"start_beat": 40}}])
    assert transport.calls == []
