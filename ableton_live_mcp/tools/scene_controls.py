"""Native scene tempo and meter overrides."""

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ._creator_controls import creator_call


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_scene_musical_settings(ctx: Context, scene_index: int) -> str:
    """Read scene tempo and time-signature overrides and their enable flags.
    Disabled values can report -1. This does not launch the scene.
    """
    return creator_call("get_scene_musical_settings", scene_index=scene_index)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def set_scene_musical_settings(ctx: Context, scene_index: int, settings: dict) -> str:
    """Set tempo (20..999 BPM), tempo_enabled (bool), time_signature_numerator
    (1..99), time_signature_denominator (1,2,4,8,16), or time_signature_enabled (bool).
    Explicit enable flags apply after values. Does not launch the scene.
    Returns readback. Execute separately, outside batch_commands.
    """
    return creator_call("set_scene_musical_settings", scene_index=scene_index, settings=settings)
