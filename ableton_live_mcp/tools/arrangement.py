"""Arrangement view, timeline placement, and clip automation."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import ClipIndex, DeviceIndex, DeviceParameter, TrackIndex


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def inspect_arrangement(ctx: Context, reference: str | None = None) -> str:
    """Inspect Arrangement clips directly, including notes and source properties.

    Omit reference to enumerate tracks and clips. Use returned scoped references,
    not list indices, for later edits. Timeline positions are absolute beats.
    Source markers are beats for MIDI/warped audio, seconds for unwarped audio.
    This never launches clips or changes playback. Requires Live 12.4 or later.
    """
    result = get_ableton_connection().send_command("arrangement_inspect", {"reference": reference})
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_arrangement_edit(ctx: Context, request: dict) -> str:
    """Validate an edit without changing Live; return a single-use execution plan.

    request requires reference and operation. duplicate takes destination (absolute
    beats); copies are same-track and reject all overlaps. edit_notes takes add,
    updates (each with note_id), and remove_ids. replace_notes takes notes and
    explicitly replaces all notes. properties takes a properties dictionary.
    warp_marker takes action add/move/remove. add takes marker {beat_time,
    optional sample_time}; move takes beat_time and destination_beat; remove
    takes beat_time. Sample times are source seconds, marker beats source-relative.
    Terminal reference-marker edits and invalid warp segment tempos are rejected.
    delete explicitly removes the referenced Arrangement clip.
    trim takes absolute start/end inside the clip. move takes destination.
    split takes an absolute beat; a split on either boundary is a no-op.
    These operations use native holding copies and temporary Session clips,
    never new tracks. They require separate reconstruction authorization.
    automation takes start/end, a scoped parameter_reference from inspection,
    points [{time, value}] including both boundaries, and interpolation linear
    or step. Require snapshot_path, snapshot_sha256, snapshot_confirmed_current
    after save-and-inspect. Supported parameters are enabled continuous mixer
    and device parameters on regular tracks with audio output. Curved writes,
    returns, master automation, and destructive overlap policies are rejected.
    Source markers do not trim timeline bounds. Existing timeline automation
    remains at absolute positions during clip edits; it does not move with clips.
    Execute only after explicit user authorization and a saved recovery point.
    """
    result = get_ableton_connection().send_command("arrangement_preview", request)
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def execute_arrangement_edit(
    ctx: Context,
    plan: str,
    authorized: bool = False,
    recovery_point: str = "",
    reconstruction_authorized: bool = False,
    cancel: bool = False,
) -> str:
    """Execute a previewed edit. authorized requires explicit user approval.

    recovery_point must be an existing saved .als file. A changed target track
    invalidates the plan. Audio reconstruction can return status prepared:
    invoke this tool again with the same plan after Live's next update tick,
    or set cancel=true to remove owned temporary clips. Preparation has its own
    undo step; the Arrangement mutation requests one further undo group.
    Inspect observed state and pending_properties after completion. A nonempty
    cleanup_warnings list requires review. On partial failure, stop edits and
    use Undo or reopen the recovery Set. Completed plans cannot be reused.
    """
    result = get_ableton_connection().send_command(
        "arrangement_execute",
        {
            "plan": plan,
            "authorized": authorized,
            "recovery_point": recovery_point,
            "reconstruction_authorized": reconstruction_authorized,
            "cancel": cancel,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def switch_to_arrangement_view(ctx: Context) -> str:
    """Switch Ableton's main window to the Arrangement view.

    Parameters:
    """
    ableton = get_ableton_connection()
    ableton.send_command("switch_to_arrangement_view")
    return "Switched to Arrangement view"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_arrangement_time(ctx: Context, time: float) -> str:
    """
    Move the arrangement playhead to a specific position.

    Parameters:
    - time: Position in beats from the start of the arrangement (e.g. 8.0 = bar 3 in 4/4)
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("set_current_song_time", {"time": time})
    return f"Playhead moved to beat {result.get('current_song_time', time)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_arrangement_clips(ctx: Context, track_index: int) -> str:
    """
    List all clips placed in the Arrangement timeline for a track.

    Returns each clip's name, start_time, end_time, length, and type.

    Parameters:
    - track_index: The index of the track to inspect
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("get_arrangement_clips", {"track_index": track_index})
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def duplicate_to_arrangement(
    ctx: Context, track_index: int, clip_index: int, destination_time: float
) -> str:
    """
    Copy a Session-view clip into the Arrangement timeline.

    OVERWRITES whatever already occupies the destination range on that track
    (like recording over tape) - this is also the supported way to REPLACE a
    section. Uses Live's track.duplicate_clip_to_arrangement() API (Live 11/12).
    The clip is placed at destination_time beats from the start of the
    arrangement on the same track it lives in.

    Typical workflow:
      1. create_clip / add_notes_to_clip to build a Session clip
      2. Call duplicate_to_arrangement once per bar/section you need
      3. Call switch_to_arrangement_view to confirm the result in Live

    Parameters:
    - track_index:       Index of the track that owns the Session clip
    - clip_index:        Index of the clip slot in that track (Session view)
    - destination_time:  Beat position in the arrangement to place the clip
                         (e.g. 0.0 = start, 8.0 = bar 3 in 4/4)
    """
    ableton = get_ableton_connection()
    result = ableton.send_command(
        "duplicate_session_clip_to_arrangement",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "destination_time": destination_time,
        },
    )
    clip_name = result.get("clip_name", "clip")
    track_name = result.get("track_name", f"track {track_index}")
    return (
        f"Duplicated '{clip_name}' from Session slot {clip_index} "
        f"on '{track_name}' to arrangement at beat {destination_time}"
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_arrangement_clip(ctx: Context, track_index: int, arrangement_clip_index: int) -> str:
    """Delete a clip from the Arrangement timeline by its position in
    get_arrangement_clips' list. NOTE: indices of later clips on the same track
    shift down by one after each delete - re-read get_arrangement_clips between
    deletes.
    """
    result = get_ableton_connection().send_command(
        "delete_arrangement_clip",
        {"track_index": track_index, "arrangement_clip_index": arrangement_clip_index},
    )
    return f"Deleted arrangement clip '{result.get('name')}' ({result.get('start_time')}-{result.get('end_time')})"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def write_automation(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    parameter: str | int,
    points: list[dict[str, float]],
) -> str:
    """Write clip automation for a device parameter. points = [{"time": beats, "value": v}, ...].
    Replaces any existing envelope for that parameter on the clip."""
    r = get_ableton_connection().send_command(
        "write_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
            "points": points,
        },
    )
    return f"Wrote {r.get('point_count')} automation points for {r.get('parameter')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def clear_automation(
    ctx: Context,
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
) -> str:
    """Delete one device parameter's entire automation envelope from a clip.

    Resolve the device and parameter with get_device_parameters first. This
    removes every automation point for that parameter, not a time range; manual
    parameter state remains. Use write_automation to replace the envelope with
    new points, or undo immediately to recover an accidental clear.
    """
    r = get_ableton_connection().send_command(
        "clear_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
        },
    )
    return f"Cleared automation for {r.get('parameter')}"
