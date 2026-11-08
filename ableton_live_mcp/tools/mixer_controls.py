"""Mixer controls across regular tracks, returns, and master."""

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ._creator_controls import creator_call


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_mixer_controls(
    ctx: Context,
    target: Literal["track", "return", "master"] = "track",
    target_index: int = 0,
) -> str:
    """Inspect volume, panning, split-stereo panning, cue volume, sends, mute, and solo.
    Reports supported fields, native parameter ranges, and enable state. Master index is 0.
    Cue volume belongs to master. Split-stereo parameter availability depends on panning mode.
    """
    return creator_call("get_mixer_controls", target=target, target_index=target_index)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def set_mixer_controls(
    ctx: Context,
    settings: dict,
    target: Literal["track", "return", "master"] = "track",
    target_index: int = 0,
) -> str:
    """Set volume, panning, left_split_stereo, right_split_stereo, cue_volume (master only),
    mute/solo (bool), panning_mode (0 Stereo,1 Split Stereo), or sends ({index: native_value}).
    Inspect native ranges first; volume values are not dB. Change panning mode separately,
    then inspect before writing split-stereo parameters. No automatic clamping.
    Returns readback and unconfirmed values. Execute separately, outside batch_commands.
    """
    return creator_call(
        "set_mixer_controls", target=target, target_index=target_index, settings=settings
    )
