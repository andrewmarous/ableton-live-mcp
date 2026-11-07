"""Session launch configuration and explicit triggering."""

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ._creator_controls import creator_call


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_session_launch_settings(ctx: Context, track_index: int, clip_index: int) -> str:
    """Read Session clip launch mode, quantization, legato, and velocity sensitivity.
    Returns native enum mappings and unsupported fields. Does not launch playback.
    """
    return creator_call(
        "get_session_launch_settings", track_index=track_index, clip_index=clip_index
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def set_session_launch_settings(
    ctx: Context, track_index: int, clip_index: int, settings: dict
) -> str:
    """Set launch_mode (0..3), launch_quantization (0..14), legato (bool), or
    velocity_amount (0..1). Read enum mappings first. Does not launch playback.
    Validates the entire request before writing; returns readback and partial failures.
    Execute separately, outside batch_commands.
    """
    return creator_call(
        "set_session_launch_settings",
        track_index=track_index,
        clip_index=clip_index,
        settings=settings,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def trigger_session_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    action: Literal["fire", "press", "release"] = "fire",
    launch_quantization: int | None = None,
) -> str:
    """Explicitly launch an existing Session clip or press/release its launch button.
    Starts or queues playback. Press/release supports Gate and Repeat behavior.
    Optional launch_quantization (0..14) overrides quantization for fire only.
    Never records an empty slot. Inspect state after the launch boundary before retrying.
    """
    return creator_call(
        "trigger_session_clip",
        track_index=track_index,
        clip_index=clip_index,
        action=action,
        launch_quantization=launch_quantization,
    )
