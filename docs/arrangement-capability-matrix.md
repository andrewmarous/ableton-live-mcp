# Arrangement editing: capability investigation

Status: blocked on an approved alternative for Arrangement automation. This is not an implementation or acceptance claim.

## Decisions

- Target the latest stable Live version. Verify the exact installed version before acceptance.
- Maintain the bridge in https://github.com/andrewmarous/ableton-live-mcp.
- Keep the existing package pin until matching server and Remote Script artifacts pass verification.
- The user performs acceptance in a disposable Set before issue #14 closes.
- The user approved save-then-inspect: attempt MCP `save_set`, verify the saved file, then inspect its automation.
- If the API save is unavailable or unverified, request a manual save before snapshot inspection.
- Keep edits on existing tracks. This decision does not authorize temporary clip replacement or carrier stamping.
- Do not modify a production Set during development.

The local fork is `../ableton-live-mcp`, on branch `issue-14-arrangement`.
The starting source is upstream tag `v1.8.1`.

## Evidence and version limits

The current [official LOM reference](https://docs.cycling74.com/apiref/lom/) identifies Live 12.4.5.
Documentation version does not establish the latest stable release available to the user.

The Python runtime reference is unofficial and identifies Live 12 without an exact patch version.
Third-party experiments identify 12.4.6 and 12.4.15b1. These results require independent verification in the target installation.
Older versions are outside the initial support target. No version receives a full-support claim from this investigation.

## Capability matrix

| Requirement | Native route and evidence | Remaining work or limitation |
| --- | --- | --- |
| Duplicate audio and MIDI Arrangement clips | `Track.duplicate_clip_to_arrangement(clip, destination_time)` explicitly accepts Arrangement sources in the [Track reference](https://docs.cycling74.com/apiref/lom/track/). | Validate overlaps before copying. Same-track support first. Reject cross-track requests until preservation tests establish support. Native duplication does not establish timeline automation preservation. |
| Read and edit MIDI notes | The [Clip reference](https://docs.cycling74.com/apiref/lom/clip/) covers Session and Arrangement clips. It exposes extended note reads, additions, ID-based removal, and modifications. | Validate note IDs before mutation. Preserve extended note properties. Define an explicit replacement command. Reject edits that cause Live to modify unrelated overlapping notes. |
| Read and edit audio properties | The [Clip reference](https://docs.cycling74.com/apiref/lom/clip/) exposes source markers, gain, coarse/fine pitch, warping, and warp mode. The [Python reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime) exposes warp-marker operations. | Source markers are not timeline edges. Python marker units depend on warping. Validate available modes and source units. Never crop or rewrite source files. Shared-source effects require live verification. |
| Trim, move, and split | The [Clip reference](https://docs.cycling74.com/apiref/lom/clip/) marks `start_time` and `end_time` read-only. The [Track runtime reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Track.runtime) provides duplication and deletion, but no direct timeline split or move method. | Copy/delete and reconstruction require separate preservation contracts. Writable source markers do not prove timeline trim support. Timeline automation cannot currently be read for transfer. Loops, offsets, and boundary splits remain unverified. |
| Read and write Arrangement automation | The [Python Clip reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime) explicitly states that `automation_envelope` returns `None` for Arrangement clips. | No documented native lane/breakpoint interface was identified. Session envelopes are not Arrangement lanes. Exact inspection and preservation cannot be promised through the current bridge. |

## Automation blocker

[Producer Pal's experiments](https://github.com/adamjmurray/producer-pal/blob/main/dev/live-api/arrangement-automation.md) report the same limitation on Live 12.4.6.
Their investigation also covers the 12.4.15b1 LOM allowlist.
These are external observations, not acceptance results for this fork.

The experiments describe three alternatives:

1. Read exact breakpoints and curves from a saved `.als` snapshot.
2. Read sampled values through playhead movement and later parameter reads.
3. Write lanes through Session-envelope carrier clips, then restore displaced Arrangement clips from parked native copies.

Each alternative changes the original guarantees:

- A saved snapshot does not include unsaved edits. Snapshot identity and freshness need explicit validation.
- Playhead sampling changes Set state and cannot reconstruct exact breakpoint positions or curves.
- Carrier writes replace material temporarily. Restoration can fail after partial mutation.
- Native Arrangement copies reportedly leave timeline automation at its original position.
- The carrier route cannot clear a lane and does not cover every parameter class.

The issue forbids silent destructive reconstruction and requires review of alternatives.
No carrier stamping, GUI automation, source-file rewriting, or offline Set modification is implemented.

## Proposed decision for review

Snapshot-backed inspection now has user approval through the save-then-inspect workflow.
The transactional carrier workflow remains a separate proposal for supported track parameters.
The agent creates and removes its own temporary Session clips. The user does not prepare Session slots.
Occupied Session slots must remain unchanged.

### Save-then-inspect contract

1. Require an explicit path to the existing `.als` file. Do not guess the active Set from recent files.
2. Record the file identity, modification time, size, and content digest before the save attempt.
3. Attempt MCP `save_set` and inspect its structured `available` and `saved` result.
4. If saving is unavailable, fails, or cannot be verified, request a manual save.
5. Wait for a stable, readable saved file before parsing. Reject file changes during inspection.
6. Report the snapshot path, digest, modification time, and verification method with inspection results.
7. Reject stale snapshot-dependent mutations after further edits until another save and inspection completes.

A changed file does not establish that it belongs to the active Set.
An unchanged file does not establish that saving failed, because the Set can already be saved.
The workflow must distinguish verified snapshots from unverified files without claiming live-state equivalence.

This candidate needs approval before implementation, and it does not yet satisfy all acceptance criteria.
The approval must define snapshot freshness, parameter exclusions, recovery behavior, and the treatment of timeline automation during clip moves.

If those restrictions are unacceptable, the complete issue remains blocked on a native API or another approved editing mechanism.
Implementing the first three capabilities alone must not close the issue.

## Current code and verification

The fork contains a scoped-reference foundation and six isolated unit tests.
The tests cover unrelated edits, deleted clips, deleted tracks, Set replacement, reconnects, and unknown handles.
These references are not yet connected to command dispatch.

No Live commands ran during this investigation. No acceptance checks passed yet.
No installer changes or package-pin changes were made.

## Sources

- [Official LOM version](https://docs.cycling74.com/apiref/lom/)
- [Official Track API](https://docs.cycling74.com/apiref/lom/track/)
- [Official Clip API](https://docs.cycling74.com/apiref/lom/clip/)
- [Python Track runtime reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Track.runtime)
- [Python Clip runtime reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime)
- [Arrangement automation experiments](https://github.com/adamjmurray/producer-pal/blob/main/dev/live-api/arrangement-automation.md)
- [Session-envelope experiments](https://github.com/adamjmurray/producer-pal/blob/main/dev/live-api/clip-envelopes.md)

Search records: `/tmp/ableton-arrangement-api.json` and `/tmp/ableton-arrangement-blockers.json`.
