"""Simpler sample-region, loop, and slicing controls."""

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ._creator_controls import creator_call


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_simpler_sample_controls(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_path: list[list[int]] | None = None,
) -> str:
    """Inspect Simpler sample metadata, markers, slices, playback and slicing settings,
    and available loop parameters with native ranges. Markers/slices use sample frames.
    Optional chain_path contains nested [chain_index, device_index] pairs from the root device.
    Empty/multisample Simpler and other instruments return structured errors.
    """
    return creator_call(
        "get_simpler_sample_controls",
        track_index=track_index,
        device_index=device_index,
        chain_path=chain_path,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def set_simpler_sample_controls(
    ctx: Context,
    track_index: int,
    device_index: int,
    device_settings: dict | None = None,
    sample_settings: dict | None = None,
    loop_parameters: dict | None = None,
    chain_path: list[list[int]] | None = None,
) -> str:
    """Set Simpler device settings: playback_mode (0 Classic,1 One-Shot,2 Slicing),
    slicing_playback_mode (0 Mono,1 Poly,2 Thru), pad_slicing/retrigger (bool), voices (1..32).
    Sample settings: start_marker/end_marker (integer sample frames), slicing_style
    (0 Transient,1 Beat,2 Region,3 Manual), slicing_sensitivity (0..1),
    slicing_beat_division (0..10; inspect mapping), slicing_region_count (2..64).
    loop_parameters maps inspected parameter indices to native values in Classic mode.
    Change playback mode separately and inspect before mode-specific edits.
    Does not crop, replace, or rewrite the source audio file. Returns observed settings.
    Execute separately, outside batch_commands. Inspect unconfirmed values before retrying.
    """
    return creator_call(
        "set_simpler_sample_controls",
        track_index=track_index,
        device_index=device_index,
        device_settings=device_settings,
        sample_settings=sample_settings,
        loop_parameters=loop_parameters,
        chain_path=chain_path,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def edit_simpler_slice(
    ctx: Context,
    track_index: int,
    device_index: int,
    action: Literal["insert", "move", "remove"],
    position: int,
    destination: int | None = None,
    chain_path: list[list[int]] | None = None,
) -> str:
    """Insert, move, or remove one slice in Manual Slicing mode.
    Positions are integer sample frames, not beats or seconds. Move requires destination.
    Read current slices first. The initial slice is protected. No audio file is rewritten.
    Returns observed slices; inspect partial failures before retrying. Execute outside batch_commands.
    """
    return creator_call(
        "edit_simpler_slice",
        track_index=track_index,
        device_index=device_index,
        action=action,
        position=position,
        destination=destination,
        chain_path=chain_path,
    )
