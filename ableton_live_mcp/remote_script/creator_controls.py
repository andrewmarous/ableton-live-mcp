"""Native creator controls. Bundled into the installed Remote Script."""

import math


class CreatorControlError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CreatorControls:
    LAUNCH = {
        "launch_mode": (0, 3),
        "launch_quantization": (0, 14),
        "legato": "bool",
        "velocity_amount": (0.0, 1.0),
    }
    SCENE = {
        "tempo": (20.0, 999.0),
        "tempo_enabled": "bool",
        "time_signature_numerator": (1, 99),
        "time_signature_denominator": (1, 16),
        "time_signature_enabled": "bool",
    }
    DEVICE = {
        "playback_mode": (0, 2),
        "slicing_playback_mode": (0, 2),
        "pad_slicing": "bool",
        "retrigger": "bool",
        "voices": (1, 32),
    }
    SAMPLE = {
        "start_marker": (0, 2147483647),
        "end_marker": (0, 2147483647),
        "slicing_style": (0, 3),
        "slicing_sensitivity": (0.0, 1.0),
        "slicing_beat_division": (0, 10),
        "slicing_region_count": (2, 64),
    }

    def __init__(self, host):
        self.host = host

    @staticmethod
    def fail(code, message):
        raise CreatorControlError(code, message)

    @classmethod
    def index(cls, value, name):
        if type(value) is not int or value < 0:
            cls.fail("invalid_index", name + " must be a nonnegative integer")
        return value

    @classmethod
    def value(cls, value, spec, name):
        if spec == "bool":
            if type(value) is not bool:
                cls.fail("invalid_value", name + " requires a boolean")
        elif (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not spec[0] <= value <= spec[1]
            or (type(spec[0]) is int and type(value) is not int)
        ):
            cls.fail("invalid_value", name + " is outside its native range or has the wrong type")
        return value

    @classmethod
    def available(cls, obj, name):
        try:
            return getattr(obj, name)
        except (AttributeError, RuntimeError):
            cls.fail("unsupported", name + " is unavailable on this object or Live version")

    @staticmethod
    def properties(obj, fields):
        values, missing = {}, []
        for key in fields:
            try:
                value = getattr(obj, key)
                values[key] = value if isinstance(value, (str, int, float, bool)) else int(value)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                missing.append(key)
        return {"values": values, "unsupported": missing}

    def changes(self, obj, settings, specs):
        if not isinstance(settings, dict) or not settings:
            self.fail("invalid_settings", "Provide a nonempty settings object")
        tasks = []
        for name, value in settings.items():
            if name not in specs:
                self.fail("unknown_field", "Unknown setting: " + str(name))
            self.available(obj, name)
            tasks.append((obj, name, self.value(value, specs[name], name), name))
        return tasks

    def apply(self, tasks, read):
        completed = []
        self.host._song.begin_undo_step()
        try:
            for obj, name, value, label in tasks:
                setattr(obj, name, value)
                completed.append(label)
            observed = read()
            unconfirmed = []
            for obj, name, requested, label in tasks:
                actual = getattr(obj, name)
                if actual != requested and not (
                    isinstance(actual, (int, float))
                    and isinstance(requested, (int, float))
                    and abs(actual - requested) < 1e-6
                ):
                    unconfirmed.append(
                        {"setting": label, "requested": requested, "observed": actual}
                    )
            return {
                "status": "updated",
                "completed": completed,
                "observed": observed,
                "unconfirmed": unconfirmed,
            }
        except Exception as exc:
            try:
                observed = read()
            except Exception as read_error:
                observed = {"error": str(read_error)}
            return {
                "status": "partial_failure",
                "completed": completed,
                "error": str(exc),
                "observed": observed,
                "recovery": "Inspect state before retrying. Use Undo.",
            }
        finally:
            self.host._song.end_undo_step()

    def dispatch(self, action, params):
        try:
            return getattr(self, action)(params)
        except CreatorControlError as exc:
            return {"status": "error", "code": exc.code, "error": str(exc)}
        except (AttributeError, RuntimeError, IndexError, TypeError, ValueError) as exc:
            return {"status": "error", "code": "unavailable_or_invalid_target", "error": str(exc)}

    def clip(self, p):
        clip = self.host._get_clip(
            self.index(p.get("track_index"), "track_index"),
            self.index(p.get("clip_index"), "clip_index"),
        )
        if not self.available(clip, "is_session_clip"):
            self.fail("invalid_target", "Select a Session clip")
        return clip

    def get_launch(self, p):
        result = self.properties(self.clip(p), self.LAUNCH)
        result["enums"] = {
            "launch_mode": {"trigger": 0, "gate": 1, "toggle": 2, "repeat": 3},
            "launch_quantization": {
                "global": 0,
                "none": 1,
                "8_bars": 2,
                "4_bars": 3,
                "2_bars": 4,
                "1_bar": 5,
                "1/2": 6,
                "1/2T": 7,
                "1/4": 8,
                "1/4T": 9,
                "1/8": 10,
                "1/8T": 11,
                "1/16": 12,
                "1/16T": 13,
                "1/32": 14,
            },
        }
        return result

    def set_launch(self, p):
        tasks = self.changes(self.clip(p), p.get("settings"), self.LAUNCH)
        return self.apply(tasks, lambda: self.get_launch(p))

    def trigger(self, p):
        clip = self.clip(p)
        action = p.get("action", "fire")
        quantization = p.get("launch_quantization")
        if action not in ("fire", "press", "release"):
            self.fail("invalid_action", "Use fire, press, or release")
        if quantization is not None:
            self.value(quantization, (0, 14), "launch_quantization")
            if action != "fire":
                self.fail("invalid_settings", "Quantization override requires fire")
        if clip.is_recording:
            self.fail("recording", "Do not trigger a recording clip")
        if action == "fire":
            slot = self.host._get_track(p["track_index"]).clip_slots[p["clip_index"]]
            if quantization is None:
                slot.fire()
            else:
                slot.fire(launch_quantization=quantization)
        else:
            self.available(clip, "set_fire_button_state")(action == "press")
        return {
            "status": "triggered",
            "action": action,
            "observed": self.properties(clip, ["is_playing", "is_triggered", "is_recording"]),
            "note": "Launch may be queued. Inspect again after the quantization boundary.",
        }

    def scene(self, p):
        return self.host._get_scene(self.index(p.get("scene_index"), "scene_index"))

    def get_scene(self, p):
        result = self.properties(self.scene(p), self.SCENE)
        result["note"] = (
            "Disabled overrides can report -1. Reading settings does not launch the scene."
        )
        return result

    def set_scene(self, p):
        settings = p.get("settings")
        tasks = self.changes(self.scene(p), settings, self.SCENE)
        if "time_signature_denominator" in settings and settings[
            "time_signature_denominator"
        ] not in (1, 2, 4, 8, 16):
            self.fail("invalid_value", "Time signature denominator must be 1, 2, 4, 8, or 16")
        # Assign explicit enable flags last; native value setters may enable an override.
        tasks.sort(key=lambda task: task[1].endswith("_enabled"))
        return self.apply(tasks, lambda: self.get_scene(p))

    def simpler(self, p):
        device = self.host._get_device(
            self.index(p.get("track_index"), "track_index"),
            self.index(p.get("device_index"), "device_index"),
        )
        path = p.get("chain_path", [])
        if not isinstance(path, list) or len(path) > 16:
            self.fail("invalid_target", "chain_path must contain at most 16 [chain, device] pairs")
        for pair in path:
            if not isinstance(pair, list) or len(pair) != 2:
                self.fail(
                    "invalid_target", "Each chain_path entry must be [chain_index, device_index]"
                )
            device = device.chains[self.index(pair[0], "chain_index")].devices[
                self.index(pair[1], "device_index")
            ]
        if device.class_name != "OriginalSimpler":
            self.fail("invalid_target", "Selected device is not Simpler")
        if self.available(device, "multi_sample_mode"):
            self.fail("unsupported", "Multisample Simpler is not supported")
        sample = self.available(device, "sample")
        if sample is None:
            self.fail("no_sample", "Load a sample into Simpler first")
        return device, sample

    def editable_simpler(self, p):
        track = self.host._get_track(self.index(p.get("track_index"), "track_index"))
        if getattr(track, "is_frozen", False):
            self.fail("frozen_track", "Unfreeze the target track explicitly before editing Simpler")
        return self.simpler(p)

    @staticmethod
    def is_loop_parameter(param):
        return "loop" in getattr(param, "original_name", param.name).lower()

    @staticmethod
    def parameter_info(param, index):
        return {
            "index": index,
            "name": param.name,
            "value": param.value,
            "min": param.min,
            "max": param.max,
            "is_enabled": bool(param.is_enabled),
            "is_quantized": bool(param.is_quantized),
        }

    def get_simpler(self, p):
        device, sample = self.simpler(p)
        return {
            "device": self.properties(device, self.DEVICE),
            "sample": self.properties(sample, [*self.SAMPLE, "length", "sample_rate", "file_path"]),
            "slices": list(sample.slices),
            "marker_units": "sample_frames",
            "loop_parameters": [
                self.parameter_info(param, i)
                for i, param in enumerate(device.parameters)
                if self.is_loop_parameter(param)
            ],
            "enums": {
                "playback_mode": {"classic": 0, "one_shot": 1, "slicing": 2},
                "slicing_style": {"transient": 0, "beat": 1, "region": 2, "manual": 3},
                "slicing_playback_mode": {"mono": 0, "poly": 1, "thru": 2},
                "slicing_beat_division": [
                    "1/16",
                    "1/16T",
                    "1/8",
                    "1/8T",
                    "1/4",
                    "1/4T",
                    "1/2",
                    "1/2T",
                    "1_bar",
                    "2_bars",
                    "4_bars",
                ],
            },
        }

    def set_simpler(self, p):
        device, sample = self.editable_simpler(p)
        tasks = []
        for key, obj, specs in [
            ("device_settings", device, self.DEVICE),
            ("sample_settings", sample, self.SAMPLE),
        ]:
            settings = p.get(key, {})
            if not isinstance(settings, dict):
                self.fail("invalid_settings", key + " must be a settings object")
            if settings:
                tasks.extend(self.changes(obj, settings, specs))
        settings = p.get("sample_settings", {})
        start, end = (
            settings.get("start_marker", sample.start_marker),
            settings.get("end_marker", sample.end_marker),
        )
        if not 0 <= start < end <= sample.length:
            self.fail("invalid_range", "Require 0 <= start_marker < end_marker <= sample.length")
        if start >= sample.end_marker:
            tasks.sort(key=lambda task: task[1] != "end_marker")
        elif end <= sample.start_marker:
            tasks.sort(key=lambda task: task[1] != "start_marker")
        slicing = any(k.startswith("slicing_") for k in settings)
        ds = p.get("device_settings", {})
        slicing = slicing or "slicing_playback_mode" in ds or "pad_slicing" in ds
        if slicing and (device.playback_mode != 2 or ds.get("playback_mode", 2) != 2):
            self.fail("wrong_mode", "Set Slicing mode, inspect again, then edit slicing settings")
        loops = p.get("loop_parameters", {})
        if not isinstance(loops, dict):
            self.fail(
                "invalid_settings", "loop_parameters must map inspected parameter indices to values"
            )
        if loops and (device.playback_mode != 0 or ds.get("playback_mode", 0) != 0):
            self.fail("wrong_mode", "Loop parameters require Classic mode; change mode separately")
        for key, value in loops.items():
            if not str(key).isdigit():
                self.fail("invalid_index", "Use a loop parameter index returned by inspection")
            param = device.parameters[int(key)]
            if not self.is_loop_parameter(param) or not param.is_enabled:
                self.fail("unsupported", "Selected parameter is not an enabled loop parameter")
            self.value(value, (float(param.min), float(param.max)), param.name)
            if param.is_quantized and value != int(value):
                self.fail("invalid_value", "Quantized loop parameters require integer values")
            tasks.append((param, "value", value, "loop_parameter:" + str(key)))
        if not tasks:
            self.fail(
                "invalid_settings", "Provide device_settings, sample_settings, or loop_parameters"
            )
        return self.apply(tasks, lambda: self.get_simpler(p))

    def edit_slice(self, p):
        device, sample = self.editable_simpler(p)
        if device.playback_mode != 2 or sample.slicing_style != 3:
            self.fail("wrong_mode", "Slice edits require Slicing playback and Manual slicing style")
        action = p.get("action")
        if action not in ("insert", "move", "remove"):
            self.fail("invalid_action", "Use insert, move, or remove")
        position = self.value(p.get("position"), (0, int(sample.length)), "position")
        slices = list(sample.slices)
        if action != "insert" and position not in slices:
            self.fail("stale_slice", "Source slice does not exist; inspect again")
        if action != "insert" and slices and position == slices[0]:
            self.fail("protected_slice", "The first slice cannot be moved or removed by this tool")
        destination = position
        if action == "move":
            destination = self.value(p.get("destination"), (0, int(sample.length)), "destination")
        if action != "remove":
            if not sample.start_marker <= destination < sample.end_marker:
                self.fail(
                    "invalid_range", "Slice destination must lie within the active sample region"
                )
            if destination in slices:
                self.fail("slice_collision", "A slice already exists at the destination")
        method = self.available(sample, action + "_slice")
        args = (position, destination) if action == "move" else (position,)
        self.host._song.begin_undo_step()
        try:
            method(*args)
            return {"status": "updated", "observed": self.get_simpler(p)}
        except Exception as exc:
            return {
                "status": "partial_failure",
                "error": str(exc),
                "recovery": "Inspect slices before retrying. Use Undo if needed.",
            }
        finally:
            self.host._song.end_undo_step()

    def mixer(self, p):
        target = p.get("target", "track")
        index = self.index(p.get("target_index", 0), "target_index")
        if target not in ("track", "return", "master") or (target == "master" and index != 0):
            self.fail("invalid_target", "Use track, return, or master (master index must be 0)")
        track = self.host._resolve_track(index, target)
        return track, track.mixer_device

    def get_mixer(self, p):
        track, mixer = self.mixer(p)
        params = {}
        missing = []
        for key in ("volume", "panning", "left_split_stereo", "right_split_stereo", "cue_volume"):
            try:
                params[key] = self.parameter_info(getattr(mixer, key), 0)
            except (AttributeError, RuntimeError):
                missing.append(key)
        return {
            "parameters": params,
            "mixer": self.properties(mixer, ["panning_mode"]),
            "track": self.properties(track, ["mute", "solo"]),
            "unsupported": missing,
            "sends": [self.parameter_info(param, i) for i, param in enumerate(mixer.sends)],
            "panning_modes": {"stereo": 0, "split_stereo": 1},
        }

    def set_mixer(self, p):
        track, mixer = self.mixer(p)
        settings = p.get("settings")
        if not isinstance(settings, dict) or not settings:
            self.fail("invalid_settings", "Provide nonempty mixer settings")
        tasks = []
        for key, value in settings.items():
            if key in ("mute", "solo", "panning_mode"):
                obj = mixer if key == "panning_mode" else track
                spec = (0, 1) if key == "panning_mode" else "bool"
                tasks.extend(self.changes(obj, {key: value}, {key: spec}))
                continue
            if key not in (
                "volume",
                "panning",
                "left_split_stereo",
                "right_split_stereo",
                "cue_volume",
                "sends",
            ):
                self.fail("unknown_field", "Unknown mixer setting: " + key)
            if key == "cue_volume" and p.get("target", "track") != "master":
                self.fail("invalid_target", "Cue volume belongs to the master mixer")
            if key in ("left_split_stereo", "right_split_stereo") and (
                mixer.panning_mode != 1 or settings.get("panning_mode", 1) != 1
            ):
                self.fail("wrong_mode", "Set split stereo mode separately, then inspect again")
            entries = [(key, self.available(mixer, key), value)] if key != "sends" else []
            if key == "sends":
                if not isinstance(value, dict):
                    self.fail("invalid_settings", "sends must map send indices to native values")
                for i, v in value.items():
                    if not str(i).isdigit():
                        self.fail("invalid_index", "Invalid send index")
                    entries.append(("send:" + str(i), mixer.sends[int(i)], v))
            for label, param, native in entries:
                if not param.is_enabled:
                    self.fail("disabled_parameter", label + " is disabled")
                self.value(native, (float(param.min), float(param.max)), label)
                tasks.append((param, "value", native, label))
        if not tasks:
            self.fail("invalid_settings", "No mixer changes requested")
        return self.apply(tasks, lambda: self.get_mixer(p))
