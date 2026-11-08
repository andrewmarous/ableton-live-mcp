"""FastMCP application instance and server lifecycle."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread
from mcp.server.fastmcp import FastMCP

from .connection import disconnect_ableton, get_ableton_connection

logger = logging.getLogger("AbletonMCPServer")


async def _connect_on_startup() -> None:
    """Best-effort connection warm-up; every tool call lazily connects anyway."""
    try:
        await anyio.to_thread.run_sync(get_ableton_connection)
        logger.info("Successfully connected to Ableton on startup")
    except Exception as e:
        logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
        logger.warning("Make sure the Ableton Remote Script is running")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Warm up the Ableton connection on startup and clean up on shutdown."""
    logger.info("AbletonMCP server starting up")
    # Fire-and-forget: connect retries take up to ~17 s when Live is closed and
    # must not delay the MCP handshake.
    warmup = asyncio.ensure_future(_connect_on_startup())
    try:
        yield {}
    finally:
        warmup.cancel()
        disconnect_ableton()
        logger.info("AbletonMCP server shut down")


INSTRUCTIONS = """Control Ableton Live locally. Read session/target state before editing and verify
changes afterwards. Replacing notes, samples or arrangement regions can destroy
musical content; preserve the original and confirm targets. A timeout is not proof
an edit failed: inspect state before retrying. Offline .als tools work with Live
closed. Mix heuristics are not measured audio; decoded sample peak/RMS is not LUFS.

Conventions:

- ALL indices are 0-based: track_index, clip_index (session slot), scene_index,
  return_index (0 = Return A), device_index (position in the track's chain).
- Arrangement positions use absolute BEATS (quarter notes). Note times use source-relative beats.
  Source markers in unwarped audio use native source units, not Arrangement bounds.
- Volume faders are Live's normalized 0.0-1.0 range where 0.85 = 0 dB unity.
  Device parameters use each parameter's native range - call get_device_parameters
  first and read min/max/display before setting values.
- MIDI notes: pitch 0-127 (60 = C3 in Live's naming), velocity 0-127,
  start_time/duration in beats relative to clip start.
- Native creator controls expose Session launch settings, scene tempo/meter overrides,
  Simpler sample regions/slices/loops, and track/return/master mixer controls.
  Inspect supported fields and native ranges first. Markers and slices in Simpler use sample frames.
  Change playback/panning mode separately, then inspect before mode-specific edits.
  Creator setting tools manage their own undo groups; execute them outside batch_commands.
  Stop on partial failures. Inspect unconfirmed readbacks rather than blindly retrying.
- Existing Arrangement clips: inspect_arrangement -> preview_arrangement_edit ->
  execute_arrangement_edit. Require explicit authorization and a saved recovery .als.
  Obtain separate reconstruction approval for trim/move/split/automation.
  If execution returns prepared, execute that plan after the next Live update or cancel it.
  Clip edits keep automation at absolute positions. Move automation through separate explicit edits.
- Arrangement automation: confirm the saved Set path, then save_and_inspect_arrangement_automation.
  If saving cannot be verified, request manual saving and confirmation before snapshot inspection.
  Snapshots exclude unsaved changes. Read live parameter references before automation writes.
- New material workflow: create_midi_track -> load_instrument_or_effect (or
  search_browser + load) -> create_clip -> add_notes_to_clip -> fire_clip /
  duplicate_to_arrangement. Check get_session_info / get_track_info first.
- add_notes_to_clip REPLACES the clip's entire note content (use edit_notes for
  incremental changes). duplicate_to_arrangement OVERWRITES whatever overlaps the
  destination range. delete_track / delete_arrangement_clip shift later indices.
- Use play(start_beat=0) to start from the beginning, or play(start_beat=N) for a passage.
  Omit start_beat to resume the actual stopped position. Use stop to stop transport.
  These tools return verified state and run separately from batch_commands.
  Explicit starts can briefly sound the old position before seeking; use pre-roll.
- Use batch_commands for ordinary multi-step edits. Execute Arrangement plans separately;
  they manage their own validation, preparation, recovery, and undo groups.
- If every command suddenly times out, a modal dialog is open in Live (e.g. the
  trial nag) - it freezes the Remote Script until dismissed. Ask the user to
  press Enter in Live, then retry.
- undo/redo are available; prefer batch_commands so one undo reverts a whole edit.
"""

mcp = FastMCP("Ableton Live MCP Server", lifespan=server_lifespan, instructions=INSTRUCTIONS)
