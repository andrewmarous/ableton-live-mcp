"""Arrangement command implementation; bundled into the installed Remote Script.

All positions in this interface are absolute beats unless a source unit is named.
Only native copies are used for clip content. No source audio files are rewritten.
"""

import hashlib
import json
import math
import os
import uuid


class ArrangementError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArrangementError("invalid_range", name + " must be numeric")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ArrangementError("invalid_range", name + " is outside its finite range")
    return value


def _property_matches(observed, requested):
    if isinstance(requested, bool) or isinstance(observed, bool):
        return type(observed) is type(requested) and observed == requested
    if isinstance(observed, (int, float)) and isinstance(requested, (int, float)):
        return math.isclose(observed, requested, rel_tol=1e-6, abs_tol=1e-7)
    return observed == requested


class ArrangementEngine:
    MAX_BEAT = 1576800.0
    NOTE_FIELDS = (
        "note_id",
        "pitch",
        "start_time",
        "duration",
        "velocity",
        "mute",
        "probability",
        "velocity_deviation",
        "release_velocity",
    )
    PROPERTIES = (
        "start_marker",
        "end_marker",
        "loop_start",
        "loop_end",
        "looping",
        "gain",
        "pitch_coarse",
        "pitch_fine",
        "warping",
        "warp_mode",
    )

    def __init__(self, host):
        self.host = host
        self.song = host._song
        self.refs = {}
        self.plans = {}
        self._progress = []
        self._parked = []
        self._scratch = []
        self._cleanup_warnings = []
        self.prepared = {}
        self._ready_carriers = []
        self._preparing_audio = False

    def _version(self):
        app = self.host.application()
        version = (app.get_major_version(), app.get_minor_version(), app.get_bugfix_version())
        if version < (12, 4, 0):
            raise ArrangementError(
                "unsupported_version", "Arrangement editing requires Live 12.4 or later"
            )
        return version

    def _handle(self, track, clip=None):
        for key, pair in list(self.refs.items()):
            try:
                if pair == (track, clip):
                    return key
            except (RuntimeError, ReferenceError):
                self.refs.pop(key, None)
        key = uuid.uuid4().hex
        self.refs[key] = (track, clip)
        return key

    def _resolve(self, handle, require_clip=True):
        pair = self.refs.get(handle)
        if pair is None:
            raise ArrangementError("stale_reference", "Unknown reference; inspect again")
        track, clip = pair
        try:
            valid = track in self.song.tracks
            if require_clip:
                valid = valid and clip is not None and clip in track.arrangement_clips
        except (RuntimeError, ReferenceError):
            valid = False
        if not valid:
            raise ArrangementError("stale_reference", "Target no longer exists; inspect again")
        return track, clip

    def _notes(self, clip):
        if not clip.is_midi_clip:
            return None
        if not hasattr(clip, "get_all_notes_extended"):
            raise ArrangementError("unsupported_api", "Extended MIDI note API is required")
        return [
            {key: getattr(note, key) for key in self.NOTE_FIELDS if hasattr(note, key)}
            for note in clip.get_all_notes_extended()
        ]

    def _clip(self, track, clip, notes=True):
        result = {
            "reference": self._handle(track, clip),
            "name": clip.name,
            "track_reference": self._handle(track),
            "track_name": track.name,
            "type": "midi" if clip.is_midi_clip else "audio",
            "start": clip.start_time,
            "end": clip.end_time,
            "source_marker_unit": "seconds" if clip.is_audio_clip and not clip.warping else "beats",
        }
        for key in self.PROPERTIES:
            if (
                key in ("gain", "pitch_coarse", "pitch_fine", "warping", "warp_mode")
                and not clip.is_audio_clip
            ):
                continue
            result[key] = getattr(clip, key)
        if clip.is_audio_clip:
            result["file_path"] = clip.file_path
            result["available_warp_modes"] = [int(mode) for mode in clip.available_warp_modes]
            result["warp_markers"] = [
                {"beat_time": marker["beat_time"], "sample_time": marker["sample_time"]}
                if isinstance(marker, dict)
                else {"beat_time": marker.beat_time, "sample_time": marker.sample_time}
                for marker in clip.warp_markers
            ]
            result["sample_length"] = getattr(clip, "sample_length", None)
            result["sample_rate"] = getattr(clip, "sample_rate", None)
        if notes and clip.is_midi_clip:
            result["notes"] = self._notes(clip)
            result["note_time_coordinates"] = "source-relative beats, not Arrangement beats"
        result["automation"] = "Arrangement lanes require saved-Set inspection; not clip envelopes"
        return result

    def inspect(self, params):
        version = self._version()
        if params.get("reference"):
            track, clip = self._resolve(params["reference"])
            return {"live_version": version, "clip": self._clip(track, clip)}
        tracks = []
        for index, track in enumerate(self.song.tracks):
            tracks.append(
                {
                    "index": index,
                    "reference": self._handle(track),
                    "name": track.name,
                    "clips": [self._clip(track, clip) for clip in track.arrangement_clips],
                    "parameters": self._parameter_inventory(track),
                }
            )
        return {
            "live_version": version,
            "tracks": tracks,
            "reference_lifetime": "current script and Set; deleted objects become stale",
            "prepared_plans": list(self.prepared),
        }

    def _fingerprint(self, track):
        state = {
            "clips": [self._clip(track, clip) for clip in track.arrangement_clips],
            "parameters": self._parameter_inventory(track),
        }
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    def _editable(self, track):
        if self.song.is_playing or self.song.record_mode:
            raise ArrangementError(
                "transport_running", "Stop playback and recording before an edit"
            )
        if track.is_frozen:
            raise ArrangementError("frozen_track", "Frozen tracks cannot be edited")

    def _note(self, note):
        allowed = set(self.NOTE_FIELDS) - {"note_id"}
        if set(note) - allowed:
            raise ArrangementError("invalid_notes", "Unknown note fields")
        out = dict(note)
        for field, default, low, high in (
            ("pitch", 60, 0, 127),
            ("start_time", 0, -16384, self.MAX_BEAT),
            ("duration", 0.25, 0.000001, self.MAX_BEAT),
            ("velocity", 100, 0, 127),
            ("probability", 1, 0, 1),
            ("velocity_deviation", 0, -127, 127),
            ("release_velocity", 64, 0, 127),
        ):
            out[field] = _number(out.get(field, default), low, high, field)
        if out["pitch"] != int(out["pitch"]):
            raise ArrangementError("invalid_notes", "Pitch must be an integer")
        out["pitch"] = int(out["pitch"])
        if "mute" in out and not isinstance(out["mute"], bool):
            raise ArrangementError("invalid_notes", "mute must be boolean")
        out.setdefault("mute", False)
        return out

    def _validate_notes(self, clip, request):
        existing = self._notes(clip)
        by_id = {n["note_id"]: n for n in existing}
        remove = request.get("remove_ids", [])
        updates = request.get("updates", [])
        update_ids = [n.get("note_id") for n in updates]
        selected = list(remove) + update_ids
        if any(type(note_id) is not int for note_id in selected):
            raise ArrangementError("invalid_notes", "Note IDs must be integers")
        if len(set(selected)) != len(selected) or any(n not in by_id for n in selected):
            raise ArrangementError("invalid_notes", "Note IDs are missing, repeated, or stale")
        if request["operation"] == "replace_notes":
            if remove or updates:
                raise ArrangementError("invalid_notes", "Replacement accepts only notes")
            final = [self._note(n) for n in request.get("notes", [])]
        else:
            final = [n for n in existing if n["note_id"] not in selected]
            for update in updates:
                merged = dict(by_id[update["note_id"]])
                merged.update(update)
                merged.pop("note_id")
                final.append(self._note(merged))
            final.extend(self._note(n) for n in request.get("add", []))
        # Live can shorten existing notes on a same-pitch overlap. Reject first.
        for pitch in range(128):
            notes = sorted((n for n in final if n["pitch"] == pitch), key=lambda n: n["start_time"])
            for left, right in zip(notes, notes[1:]):
                if left["start_time"] + left["duration"] > right["start_time"] + 1e-9:
                    raise ArrangementError(
                        "note_overlap", "Same-pitch note overlap can change unrelated notes"
                    )
        return final

    def _validate_properties(self, clip, properties):
        if not properties or set(properties) - set(self.PROPERTIES):
            raise ArrangementError("invalid_properties", "Unknown or empty clip properties")
        audio = {"gain", "pitch_coarse", "pitch_fine", "warping", "warp_mode"}
        if not clip.is_audio_clip and set(properties) & audio:
            raise ArrangementError("wrong_clip_type", "Audio properties require an audio clip")
        if clip.is_audio_clip and properties.get("looping", clip.looping) != clip.looping:
            if set(properties) & {"start_marker", "end_marker", "loop_start", "loop_end"}:
                raise ArrangementError(
                    "mode_change", "Change looping separately, then inspect before setting markers"
                )
        if "warping" in properties and properties["warping"] != clip.warping:
            if set(properties) & {"start_marker", "end_marker", "loop_start", "loop_end"}:
                raise ArrangementError(
                    "unit_change", "Change warping separately, then inspect source units again"
                )
        for key, value in properties.items():
            if key in ("looping", "warping"):
                if not isinstance(value, bool):
                    raise ArrangementError("invalid_properties", key + " must be boolean")
            elif key == "warp_mode":
                if value not in [int(mode) for mode in clip.available_warp_modes]:
                    raise ArrangementError("invalid_properties", "Warp mode is unavailable")
            else:
                bounds = {"gain": (0, 1), "pitch_coarse": (-48, 48), "pitch_fine": (-50, 49)}
                low, high = bounds.get(key, (-16384, self.MAX_BEAT))
                _number(value, low, high, key)
                if key == "pitch_coarse" and value != int(value):
                    raise ArrangementError("invalid_properties", key + " must be an integer")
        for start, end in (("start_marker", "end_marker"), ("loop_start", "loop_end")):
            if properties.get(start, getattr(clip, start)) >= properties.get(
                end, getattr(clip, end)
            ):
                raise ArrangementError("invalid_range", start + " must precede " + end)

    def preview(self, request):
        self._version()
        operation = request.get("operation")
        if operation not in (
            "duplicate",
            "edit_notes",
            "replace_notes",
            "properties",
            "warp_marker",
            "delete",
            "trim",
            "move",
            "split",
            "automation",
        ):
            raise ArrangementError(
                "unsupported_operation",
                "Operation requires a reviewed reconstruction implementation",
            )
        track, clip = self._resolve(request.get("reference"))
        self._editable(track)
        if request.get("destination_track_reference", self._handle(track)) != self._handle(track):
            raise ArrangementError(
                "cross_track_unsupported", "Cross-track copies are not supported"
            )
        ranges = [[clip.start_time, clip.end_time]]
        if operation == "duplicate":
            start = _number(request.get("destination"), 0, self.MAX_BEAT, "destination")
            end = start + clip.end_time - clip.start_time
            if end > self.MAX_BEAT:
                raise ArrangementError(
                    "invalid_range", "Destination end exceeds the Arrangement limit"
                )
            overlaps = [
                c for c in track.arrangement_clips if c.start_time < end and c.end_time > start
            ]
            if overlaps:
                raise ArrangementError(
                    "destination_overlap",
                    "Destination overlaps clips; destructive overwrite is not supported",
                )
            ranges.append([start, end])
        elif operation == "warp_marker":
            self._validate_warp_marker(clip, request)
        elif operation == "delete":
            pass
        elif operation in ("trim", "move", "split", "automation"):
            ranges = self._validate_complex(track, clip, request)
        elif operation in ("edit_notes", "replace_notes"):
            if not clip.is_midi_clip:
                raise ArrangementError("wrong_clip_type", "MIDI editing requires a MIDI clip")
            self._validate_notes(clip, request)
            for method in ("remove_notes_by_id", "apply_note_modifications", "add_new_notes"):
                if not hasattr(clip, method):
                    raise ArrangementError("unsupported_api", "Missing MIDI method: " + method)
        else:
            request = dict(request)
            properties = dict(request.get("properties", {}))
            if clip.is_audio_clip and not properties.get("looping", clip.looping):
                # Unlooped Arrangement audio uses loop_start as its effective
                # source offset. A start_marker-only write is ignored by Live.
                if "start_marker" in properties:
                    if (
                        "loop_start" in properties
                        and properties["loop_start"] != properties["start_marker"]
                    ):
                        raise ArrangementError(
                            "invalid_properties",
                            "Unlooped audio start_marker and loop_start must agree",
                        )
                    properties["loop_start"] = properties["start_marker"]
            request["properties"] = properties
            self._validate_properties(clip, properties)
        if len(self.plans) >= 64:
            disposable = next((key for key in self.plans if key not in self.prepared), None)
            if disposable is None:
                raise ArrangementError("too_many_plans", "Finish or cancel prepared edits first")
            self.plans.pop(disposable)
        token = uuid.uuid4().hex
        self.plans[token] = (json.loads(json.dumps(request)), self._fingerprint(track))
        return {
            "status": "preview",
            "plan": token,
            "operation": operation,
            "clip": self._clip(track, clip),
            "affected_ranges": ranges,
            "automation_policy": "keep_absolute_timeline; clip copies do not copy track lanes",
            "undo": "one Live undo group requested; verify during acceptance",
            "requires": (
                "explicit edit authorization and a saved recovery point"
                if request.get("require_recovery_point") is not False
                else "explicit edit authorization; recovery requirement disabled by configuration"
            ),
            "recovery_point_required": request.get("require_recovery_point") is not False,
            "reconstruction_required": operation in ("trim", "move", "split", "automation"),
            "reconstruction_contract": "docs/arrangement-reconstruction-contract.md",
            "temporary_tracks": 0,
            "normalized_properties": request.get("properties", {}),
        }

    def execute(self, params):
        self._version()
        recovery_required = params.get("require_recovery_point") is not False
        if params.get("authorized") is not True or (
            recovery_required and not params.get("recovery_point")
        ):
            raise ArrangementError(
                "authorization_required", "Authorize this edit and provide the saved recovery point"
            )
        recovery_point = params.get("recovery_point", "")
        if recovery_required or recovery_point:
            recovery = os.path.expanduser(recovery_point)
            if not recovery.lower().endswith(".als") or not os.path.isfile(recovery):
                raise ArrangementError(
                    "recovery_required", "Recovery point must be an existing saved .als file"
                )
            if os.path.getsize(recovery) == 0:
                raise ArrangementError("recovery_required", "Recovery Set is empty")
        token = params.get("plan")
        plan = self.plans.get(token)
        if plan is None:
            raise ArrangementError("stale_plan", "Preview again; plan is unknown or already used")
        request, fingerprint = plan
        if params.get("cancel") is True:
            state = self.prepared.pop(token, None)
            self.plans.pop(token, None)
            self._scratch = state["scratch"] if state else []
            self._cleanup_warnings = []
            self.song.begin_undo_step()
            try:
                self._cleanup_scratch()
            finally:
                self.song.end_undo_step()
            return {"status": "cancelled", "cleanup_warnings": self._cleanup_warnings}
        if request["operation"] in ("trim", "move", "split", "automation"):
            if params.get("reconstruction_authorized") is not True:
                raise ArrangementError(
                    "reconstruction_authorization_required",
                    "Approve the reconstruction contract and preview again",
                )
        track, clip = self._resolve(request["reference"])
        self._editable(track)
        if self._fingerprint(track) != fingerprint:
            raise ArrangementError("stale_plan", "Track changed after preview; preview again")
        # Repeat validation before the first mutation.
        check = self.preview(request)
        self.plans.pop(check["plan"])
        self._progress = []
        self._parked = []
        self._scratch = []
        self._cleanup_warnings = []
        lengths = self._carrier_lengths(clip, request)
        if clip.is_audio_clip and lengths and token not in self.prepared:
            return self._prepare_audio_carriers(track, lengths, token)
        state = self.prepared.get(token)
        if state:
            if any(not slot.has_clip or slot.clip != owned for slot, _, owned in state["scratch"]):
                raise ArrangementError(
                    "stale_resources", "Temporary clips changed; cancel this plan"
                )
            if any(not carrier.warping for _, carrier in state["carriers"]):
                return {
                    "status": "prepared",
                    "plan": token,
                    "message": "Wait for Live's warp update, then execute again",
                }
            self._scratch = state["scratch"]
            self._progress = list(state["progress"])
            self._ready_carriers = list(state["carriers"])
        self.plans.pop(token, None)
        self.song.begin_undo_step()
        try:
            operation = request["operation"]
            if operation == "duplicate":
                copied = track.duplicate_clip_to_arrangement(clip, request["destination"])
                self._progress.append("native Arrangement copy created")
                if copied is None:
                    matches = [
                        c
                        for c in track.arrangement_clips
                        if abs(c.start_time - request["destination"]) < 1e-7
                    ]
                    if len(matches) != 1:
                        raise ArrangementError(
                            "ambiguous_result", "Cannot identify the new Arrangement clip"
                        )
                    copied = matches[0]
                result = self._clip(track, copied)
            elif operation == "warp_marker":
                action = request["action"]
                if action == "add":
                    import Live

                    marker = request["marker"]
                    # Preview resolves omitted times in seconds from the warp grid.
                    # beat_to_sample_time uses native sample-frame coordinates.
                    clip.add_warp_marker(
                        Live.Clip.WarpMarker(marker["sample_time"], marker["beat_time"])
                    )
                elif action == "move":
                    clip.move_warp_marker(
                        request["beat_time"], request["destination_beat"] - request["beat_time"]
                    )
                else:
                    clip.remove_warp_marker(request["beat_time"])
                self._progress.append("edited source warp marker: " + action)
                result = self._clip(track, clip)
            elif operation == "delete":
                deleted = self._clip(track, clip)
                track.delete_clip(clip)
                self._progress.append("deleted explicitly targeted Arrangement clip")
                result = {
                    "deleted": deleted,
                    "remaining_clips": [self._clip(track, c) for c in track.arrangement_clips],
                }
            elif operation in ("trim", "move", "split", "automation"):
                result = self._complex(track, clip, request)
            elif operation in ("edit_notes", "replace_notes"):
                self._write_notes(clip, request)
                result = self._clip(track, clip)
            else:
                properties = request["properties"]
                # Move interval ends in an order that never crosses current boundaries.
                # Live can reset the source start when loop boundaries change.
                # Apply the source interval last so explicit markers survive.
                for start, end in (("loop_start", "loop_end"), ("start_marker", "end_marker")):
                    if end in properties and properties[end] > getattr(clip, start):
                        setattr(clip, end, properties[end])
                        self._progress.append("set " + end)
                    if start in properties:
                        setattr(clip, start, properties[start])
                        self._progress.append("set " + start)
                    if end in properties:
                        setattr(clip, end, properties[end])
                for key, value in properties.items():
                    if key not in ("start_marker", "end_marker", "loop_start", "loop_end"):
                        setattr(clip, key, value)
                        self._progress.append("set " + key)
                result = self._clip(track, clip)
            return {
                "status": "edited",
                "observed": result,
                "requested": request,
                "pending_properties": [
                    key
                    for key, value in request.get("properties", {}).items()
                    if not _property_matches(result.get(key), value)
                ],
                "completed": list(self._progress),
                "recovery_point": recovery_point or None,
                "recovery_point_required": recovery_required,
                "warnings": []
                if recovery_point
                else ["No saved recovery Set was supplied; recovery may be limited to Undo."],
                "cleanup_warnings": self._cleanup_warnings,
            }
        except Exception as exc:
            return {
                "status": "partial_failure",
                "code": "mutation_failed",
                "message": str(exc),
                "completed": list(self._progress),
                "recovery": (
                    "Stop edits. Inspect, then Undo or reopen the recovery Set."
                    if recovery_point
                    else (
                        "Stop edits. Inspect retained copies, then Undo. "
                        "No saved recovery Set was supplied."
                    )
                ),
                "recovery_point": recovery_point or None,
                "recovery_point_required": recovery_required,
                "retained_recovery_copies": [
                    self._clip(t, c) for t, c in self._parked if self._present(t, c)
                ],
                "cleanup_warnings": self._cleanup_warnings,
            }
        finally:
            self._cleanup_scratch()
            self._ready_carriers = []
            self.prepared.pop(token, None)
            self.song.end_undo_step()

    def _write_notes(self, clip, request):
        operation = request["operation"]
        # Live requires the native note vector returned by this API, not a
        # Python list/tuple of its elements, for apply_note_modifications.
        native_notes = clip.get_all_notes_extended()
        existing = list(native_notes)
        additions = [self._note(n) for n in request.get("add", [])]
        if operation == "replace_notes":
            additions = [self._note(n) for n in request.get("notes", [])]
            remove_ids = [n.note_id for n in existing]
        else:
            remove_ids = request.get("remove_ids", [])
        # Construct everything before making the first change; no legacy fallback.
        import Live

        specs = [Live.Clip.MidiNoteSpecification(**n) for n in additions]
        by_id = {n.note_id: n for n in existing}
        modified = []
        for update in request.get("updates", []):
            note = by_id[update["note_id"]]
            for key, value in update.items():
                if key != "note_id":
                    setattr(note, key, value)
            modified.append(note)
        if modified:
            clip.apply_note_modifications(native_notes)
            self._progress.append("updated selected notes")
        # Apply updates before removing IDs so the original native vector never
        # contains IDs deleted earlier in this request.
        if remove_ids:
            clip.remove_notes_by_id(tuple(remove_ids))
            self._progress.append("removed note IDs " + str(remove_ids))
        if specs:
            clip.add_new_notes(tuple(specs))
            self._progress.append("added notes")

    def _validate_warp_marker(self, clip, request):
        if not clip.is_audio_clip or not clip.warping:
            raise ArrangementError("wrong_clip_type", "Warp marker edits require warped audio")
        for method in ("add_warp_marker", "move_warp_marker", "remove_warp_marker"):
            if not hasattr(clip, method):
                raise ArrangementError("unsupported_api", "Missing warp marker method: " + method)
        markers = [
            dict(m)
            if isinstance(m, dict)
            else {"beat_time": m.beat_time, "sample_time": m.sample_time}
            for m in clip.warp_markers
        ]
        markers.sort(key=lambda m: m["beat_time"])
        if len(markers) < 2:
            raise ArrangementError("unsupported_api", "Cannot validate the clip's warp grid")
        action = request.get("action")
        if action == "add":
            marker = request.get("marker", {})
            if "beat_time" not in marker or set(marker) - {"beat_time", "sample_time"}:
                raise ArrangementError(
                    "invalid_marker", "Marker requires beat_time and optional sample_time"
                )
            beat = _number(marker["beat_time"], -16384, self.MAX_BEAT, "marker beat")
            if any(m["beat_time"] == beat for m in markers):
                raise ArrangementError("invalid_marker", "A marker already occupies that beat")
            pairs = list(zip(markers, markers[1:]))
            left, right = next(
                ((a, b) for a, b in pairs if a["beat_time"] <= beat <= b["beat_time"]),
                pairs[0] if beat < markers[0]["beat_time"] else pairs[-1],
            )
            inferred = left["sample_time"] + (beat - left["beat_time"]) * (
                right["sample_time"] - left["sample_time"]
            ) / (right["beat_time"] - left["beat_time"])
            seconds = _number(
                marker.get("sample_time", inferred),
                0,
                clip.sample_length / clip.sample_rate,
                "sample time in seconds",
            )
            markers.append({"beat_time": beat, "sample_time": seconds})
        elif action in ("move", "remove"):
            beat = _number(request.get("beat_time"), -16384, self.MAX_BEAT, "marker beat")
            found = [m for m in markers if m["beat_time"] == beat]
            if len(found) != 1 or found[0] == markers[-1]:
                raise ArrangementError(
                    "invalid_marker", "Marker is missing or is the terminal reference marker"
                )
            if action == "remove":
                markers.remove(found[0])
            else:
                found[0]["beat_time"] = _number(
                    request.get("destination_beat"),
                    -16384,
                    self.MAX_BEAT,
                    "destination marker beat",
                )
        else:
            raise ArrangementError("invalid_marker", "Use add, move, or remove")
        markers.sort(key=lambda m: m["beat_time"])
        if len(markers) < 2:
            raise ArrangementError("invalid_marker", "Keep at least two warp reference markers")
        for left, right in zip(markers, markers[1:]):
            seconds = right["sample_time"] - left["sample_time"]
            beats = right["beat_time"] - left["beat_time"]
            if seconds <= 0 or beats <= 0 or not 5 <= beats / seconds * 60 <= 999:
                raise ArrangementError(
                    "invalid_marker", "Warp segments must be ordered and within 5..999 BPM"
                )

        if action == "add":
            request["marker"] = {
                "beat_time": beat,
                "sample_time": next(m["sample_time"] for m in markers if m["beat_time"] == beat),
            }

    def _parameter_objects(self, track):
        mixer = getattr(track, "mixer_device", None)
        result = []
        if mixer is not None:
            for key in ("volume", "panning"):
                if hasattr(mixer, key):
                    result.append(("mixer." + key, getattr(mixer, key)))
            for index, param in enumerate(getattr(mixer, "sends", [])):
                result.append(("mixer.sends." + str(index), param))

        def visit(devices, prefix, depth=0):
            if depth > 16:
                raise ArrangementError("device_depth", "Device nesting exceeds the supported depth")
            for index, device in enumerate(devices):
                path = prefix + ".devices." + str(index)
                for number, param in enumerate(device.parameters):
                    result.append((path + ".parameters." + str(number), param))
                for chain_index, chain in enumerate(getattr(device, "chains", [])):
                    visit(chain.devices, path + ".chains." + str(chain_index), depth + 1)

        visit(getattr(track, "devices", []), "track")
        return result

    def _parameter_inventory(self, track):
        return [
            {
                "reference": self._handle(track, param),
                "path": path,
                "name": param.name,
                "min": param.min,
                "max": param.max,
                "quantized": bool(param.is_quantized),
                "value": param.value,
                "automation_state": int(param.automation_state),
            }
            for path, param in self._parameter_objects(track)
        ]

    def _parameter(self, track, reference):
        pair = self.refs.get(reference)
        if pair is not None and pair[0] == track:
            for _, param in self._parameter_objects(track):
                if param == pair[1]:
                    return param
        raise ArrangementError("stale_parameter", "Parameter reference is stale; inspect again")

    def _holding(self, length, minimum=0):
        end = max(float(getattr(self.song, "song_length", 0)), minimum)
        for track in self.song.tracks:
            for lane in [track] + list(getattr(track, "take_lanes", [])):
                for clip in getattr(lane, "arrangement_clips", []):
                    end = max(end, clip.end_time)
        start = math.ceil(end / 4) * 4 + 4
        if start + length + 4 > self.MAX_BEAT:
            raise ArrangementError(
                "no_holding_space", "No safe holding area within Live's beat limit"
            )
        return start

    def _validate_complex(self, track, clip, request):
        if not getattr(track, "has_audio_output", False):
            raise ArrangementError(
                "unsupported_track", "Reconstruction requires a regular track with audio output"
            )
        operation = request["operation"]
        start, end = clip.start_time, clip.end_time
        self._holding((end - start) * 3)
        if operation == "trim":
            left = _number(request.get("start", start), start, end, "trim start")
            right = _number(request.get("end", end), start, end, "trim end")
            if left >= right:
                raise ArrangementError("invalid_range", "Trim must retain a positive interval")
            return [[start, end], [left, right]]
        if operation == "split":
            _number(request.get("beat"), start, end, "split beat")
            return [[start, end]]
        if operation == "move":
            left = _number(request.get("destination"), 0, self.MAX_BEAT, "destination")
            right = left + end - start
            if right > self.MAX_BEAT:
                raise ArrangementError("invalid_range", "Move end exceeds the beat limit")
            self._holding((end - start) * 3, minimum=right)
            if any(
                c != clip and c.start_time < right and c.end_time > left
                for c in track.arrangement_clips
            ):
                raise ArrangementError(
                    "destination_overlap", "Move destination overlaps another clip"
                )
            return [[start, end], [left, right]]
        left = _number(request.get("start"), 0, self.MAX_BEAT, "automation start")
        right = _number(request.get("end"), 0, self.MAX_BEAT, "automation end")
        if right <= left:
            raise ArrangementError("invalid_range", "Automation interval must be positive")
        required = sum(
            c.end_time - c.start_time
            for c in track.arrangement_clips
            if c.start_time < right and c.end_time > left
        )
        self._holding(required + (right - left), minimum=right)
        param = self._parameter(track, request.get("parameter_reference"))
        if int(param.automation_state) == 2:
            raise ArrangementError(
                "automation_overridden", "Re-enable this parameter's automation before preview"
            )
        if param.is_quantized or not getattr(param, "is_enabled", True):
            raise ArrangementError("unsupported_parameter", "Use an enabled continuous parameter")
        if request.get("interpolation", "linear") not in ("linear", "step"):
            raise ArrangementError(
                "unsupported_interpolation", "Only linear and step writes are supported"
            )
        points = request.get("points", [])
        previous = None
        for point in points:
            if set(point) != {"time", "value"}:
                raise ArrangementError("invalid_points", "Points require exactly time and value")
            t = _number(point["time"], left, right, "point time")
            _number(point["value"], param.min, param.max, "point value")
            if previous is not None and t <= previous:
                raise ArrangementError("invalid_points", "Point times must strictly increase")
            previous = t
        if len(points) < 2 or points[0]["time"] != left or points[-1]["time"] != right:
            raise ArrangementError("invalid_points", "Points must include both range boundaries")
        if not request.get("snapshot_confirmed_current"):
            raise ArrangementError(
                "snapshot_required", "Save and inspect, then confirm the snapshot is current"
            )
        self._check_snapshot(request)
        try:
            from Live.Envelope import EnvelopeEvent  # noqa: F401
        except ImportError:
            raise ArrangementError(
                "unsupported_api", "Live.Envelope events require Live 12.4 or later"
            )
        return [[left, right]]

    @staticmethod
    def _check_snapshot(request):
        path = request.get("snapshot_path")
        digest = request.get("snapshot_sha256")
        if not path or not digest:
            raise ArrangementError(
                "snapshot_required", "A saved snapshot path and digest are required"
            )
        with open(path, "rb") as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ArrangementError("stale_snapshot", "Saved Set changed; save and inspect again")

    def _copy(self, track, clip, position, recovery=False):
        copied = track.duplicate_clip_to_arrangement(clip, float(position))
        if copied is None:
            candidates = [c for c in track.arrangement_clips if abs(c.start_time - position) < 1e-7]
            if len(candidates) != 1:
                raise ArrangementError("ambiguous_result", "Cannot identify a native copy")
            copied = candidates[0]
        if recovery:
            self._parked.append((track, copied))
        self._progress.append("native copy at beat " + str(position))
        return copied

    @staticmethod
    def _carrier_lengths(clip, request):
        start, end = clip.start_time, clip.end_time
        operation = request["operation"]
        if operation == "trim":
            return [
                n
                for n in (end - request.get("end", end), request.get("start", start) - start)
                if n > 1e-9
            ]
        if operation == "split" and request["beat"] not in (start, end):
            return [end - request["beat"], request["beat"] - start]
        if operation == "automation":
            return [request["end"] - request["start"]]
        return []

    def _prepare_audio_carriers(self, track, lengths, token):
        self._preparing_audio = True
        self.song.begin_undo_step()
        try:
            carriers = [(length, self._scratch_clip(track, length)) for length in lengths]
            self.prepared[token] = {
                "scratch": list(self._scratch),
                "carriers": carriers,
                "progress": list(self._progress),
            }
            return {
                "status": "prepared",
                "plan": token,
                "message": "Wait one update tick, then execute this plan again or cancel it.",
                "completed": list(self._progress),
                "arrangement_changed": False,
                "undo": "carrier preparation is a separate undo step",
            }
        except Exception as exc:
            self._cleanup_scratch()
            self.plans.pop(token, None)
            return {
                "status": "preparation_failed",
                "message": str(exc),
                "arrangement_changed": False,
                "cleanup_warnings": self._cleanup_warnings,
            }
        finally:
            self._preparing_audio = False
            self.song.end_undo_step()

    @staticmethod
    def _configure_carrier(carrier, length):
        if carrier.is_audio_clip and not carrier.warping:
            raise ArrangementError(
                "warp_pending", "Carrier must finish warping before marker writes"
            )
        # Live clamps audio loop lengths below 0.25 beats when looping is on.
        # Unlooped carriers retain exact short lengths for overlap truncation.
        carrier.looping = not carrier.is_audio_clip
        carrier.loop_start = 0.0
        carrier.loop_end = float(length)
        carrier.start_marker = 0.0
        return carrier

    def _scratch_clip(self, track, length):
        if self._ready_carriers:
            expected, carrier = self._ready_carriers.pop(0)
            if abs(expected - length) > 1e-7:
                raise ArrangementError(
                    "invalid_resources", "Carrier length differs from validated plan"
                )
            return self._configure_carrier(carrier, length)
        # Empty slots are discovered automatically; never replace a Session clip.
        slots = list(track.clip_slots)
        slot = next((s for s in slots if not s.has_clip), None)
        scene = None
        if slot is None:
            self.song.create_scene(-1)
            scene = self.song.scenes[-1]
            slot = track.clip_slots[-1]
        self._scratch.append((slot, scene, None))
        if getattr(track, "has_midi_input", False):
            slot.create_clip(length)
        else:
            # Keep this asset for Undo. No user's source file is touched.
            import os
            import tempfile
            import wave

            folder = os.path.join(tempfile.gettempdir(), "ableton-mcp-carriers")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "silence-" + uuid.uuid4().hex + ".wav")
            with wave.open(path, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(44100)
                stream.writeframes(b"\x00\x00" * 44100)
            slot.create_audio_clip(path)
            slot.clip.warping = True
            self._progress.append("Undo carrier source retained: " + path)
        carrier = slot.clip
        self._scratch[-1] = (slot, scene, carrier)
        carrier.name = "AbletonMCP temporary carrier"
        if self._preparing_audio:
            return carrier
        return self._configure_carrier(carrier, length)

    def _cleanup_scratch(self):
        for slot, scene, owned in reversed(self._scratch):
            try:
                if slot.has_clip:
                    if owned is None or slot.clip != owned:
                        self._cleanup_warnings.append(
                            "Temporary slot changed; retained it for review"
                        )
                        continue
                    slot.delete_clip()
                if scene is not None:
                    scenes = list(self.song.scenes)
                    if scene in scenes:
                        index = scenes.index(scene)
                        occupied = any(
                            index < len(t.clip_slots) and t.clip_slots[index].has_clip
                            for t in self.song.tracks
                        )
                        if occupied:
                            self._cleanup_warnings.append(
                                "Temporary scene now contains clips; retained it"
                            )
                        else:
                            self.song.delete_scene(index)
            except Exception as exc:
                self._cleanup_warnings.append(str(exc))
        self._scratch = []

    def _trim_working(self, track, clip, left, right):
        # Copies used for overlap come from Session, avoiding Arrangement-copy crashes.
        old_start, old_end = clip.start_time, clip.end_time
        if right < old_end - 1e-9:
            carrier = self._scratch_clip(track, old_end - right)
            filler = self._copy(track, carrier, right)
            track.delete_clip(filler)
            self._progress.append("trimmed working right edge")
        if left > old_start + 1e-9:
            carrier = self._scratch_clip(track, left - old_start)
            filler = self._copy(track, carrier, old_start)
            track.delete_clip(filler)
            self._progress.append("trimmed working left edge")
        candidates = [
            c
            for c in track.arrangement_clips
            if abs(c.start_time - left) < 1e-7 and abs(c.end_time - right) < 1e-7
        ]
        if len(candidates) != 1:
            raise ArrangementError(
                "trim_failed", "Live did not produce the requested timeline bounds"
            )
        result = candidates[0]
        self._parked.append((track, result))
        return result

    @staticmethod
    def _present(track, clip):
        try:
            return clip in track.arrangement_clips
        except (RuntimeError, ReferenceError):
            return False

    def _complex(self, track, clip, request):
        operation = request["operation"]
        if operation == "automation":
            return self._stamp(track, request)
        start, end = clip.start_time, clip.end_time
        if operation == "split" and request["beat"] in (start, end):
            return {"clips": [self._clip(track, clip)], "boundary_split": "no_op"}
        reserved_end = request["destination"] + end - start if operation == "move" else end
        holding = self._holding((end - start) * 3, minimum=reserved_end)
        backup = self._copy(track, clip, holding, recovery=True)
        if operation == "move":
            pieces = [(backup, request["destination"])]
        elif operation == "trim":
            left, right = request.get("start", start), request.get("end", end)
            work_start = self._holding(end - start)
            work = self._copy(track, backup, work_start, recovery=True)
            work = self._trim_working(
                track, work, work_start + left - start, work_start + right - start
            )
            pieces = [(work, left)]
        else:
            cut = request["beat"] - start
            first_start = self._holding(end - start)
            first = self._copy(track, backup, first_start, recovery=True)
            first = self._trim_working(track, first, first_start, first_start + cut)
            second_start = self._holding(end - start)
            second = self._copy(track, backup, second_start, recovery=True)
            second = self._trim_working(
                track, second, second_start + cut, second_start + end - start
            )
            pieces = [(first, start), (second, request["beat"])]
        track.delete_clip(clip)
        self._progress.append("removed original after preparing native recovery copies")
        results = [self._copy(track, source, position) for source, position in pieces]
        for t, parked in self._parked:
            if self._present(t, parked):
                t.delete_clip(parked)
        return {
            "clips": [self._clip(track, c) for c in results],
            "automation_policy": "unchanged absolute timeline",
        }

    def _stamp(self, track, request):
        from Live.Envelope import EnvelopeEvent

        start, end = request["start"], request["end"]
        param = self._parameter(track, request["parameter_reference"])
        carrier = self._scratch_clip(track, end - start)
        # Create only this parameter's envelope, never clear another lane.
        env = carrier.create_automation_envelope(param)
        if env is None:
            raise ArrangementError("unsupported_parameter", "Cannot create carrier envelope")
        points = request["points"]
        if request.get("interpolation", "linear") == "step":
            for left, right in zip(points, points[1:]):
                env.insert_step(left["time"] - start, right["time"] - left["time"], left["value"])
        else:
            for point in reversed(points):
                env.create_event(EnvelopeEvent(point["time"] - start, point["value"]))
        originals = [
            c for c in track.arrangement_clips if c.start_time < end and c.end_time > start
        ]
        restore = []
        for original in originals:
            parked = self._copy(
                track,
                original,
                self._holding(original.end_time - original.start_time, minimum=end),
                recovery=True,
            )
            restore.append((parked, original.start_time))
        for original in originals:
            track.delete_clip(original)
            self._progress.append("parked and removed overlapping original")
        stamped = self._copy(track, carrier, start)
        self._progress.append("wrote Arrangement automation over requested range")
        track.delete_clip(stamped)
        observed = [self._copy(track, parked, position) for parked, position in restore]
        for t, parked in self._parked:
            if self._present(t, parked):
                t.delete_clip(parked)
        return {
            "start": start,
            "end": end,
            "parameter_reference": request["parameter_reference"],
            "clips": [self._clip(track, c) for c in observed],
            "verification_required": "Save and inspect the lane; compare outside-range automation",
        }

    def command(self, mode, params):
        try:
            if mode == "inspect":
                return self.inspect(params)
            if mode == "preview":
                return self.preview(params)
            if mode == "execute":
                return self.execute(params)
            raise ArrangementError("invalid_operation", "Unknown Arrangement command")
        except ArrangementError as exc:
            return {"status": "error", "code": exc.code, "message": str(exc)}
        except (KeyError, TypeError, AttributeError, ValueError, OSError, RuntimeError) as exc:
            return {
                "status": "error",
                "code": "invalid_request_or_unavailable_api",
                "message": str(exc),
            }
