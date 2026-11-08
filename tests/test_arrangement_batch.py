import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "command",
    [
        "arrangement_execute",
        "execute_arrangement_edit",
        "set_session_launch_settings",
        "set_scene_musical_settings",
        "set_simpler_sample_controls",
        "edit_simpler_slice",
        "set_mixer_controls",
    ],
)
def test_arrangement_execution_is_rejected_before_any_batch_mutation(command):
    path = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AbletonMCP")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_batch")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    bridge = SimpleNamespace(_BATCH_ALIASES={})
    # No _song or command handlers: reaching a mutation would fail this test.
    with pytest.raises(ValueError, match="separately"):
        namespace["_batch"](bridge, [{"type": "create_audio_track"}, {"type": command}])
