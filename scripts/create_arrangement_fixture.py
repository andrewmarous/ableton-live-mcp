"""Generate synthetic audio assets and a recipe for a disposable Live Set.

This script never connects to Live. It refuses to replace existing files.
"""

import argparse
import hashlib
import json
import math
import struct
import wave
from pathlib import Path


def create_fixture(output):
    output = Path(output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    sample_rate, tempo, beats = 8000, 120, 64
    count = int(sample_rate * beats * 60 / tempo)
    assets = []
    for role, frequency in (("Vocal", 330), ("Guitar", 220), ("Kick", 55), ("Bass", 110)):
        path = output / (role.lower() + ".wav")
        data = bytearray()
        for index in range(count):
            seconds = index / sample_rate
            phase = (seconds * tempo / 60) % 1
            decay = math.exp(-phase * (18 if role == "Kick" else 3))
            tone = math.sin(2 * math.pi * frequency * seconds)
            data.extend(struct.pack("<h", int(6000 * decay * tone)))
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(data)
        assets.append(
            {
                "track_name": "Acceptance " + role,
                "role": role.lower(),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "looping": True,
                "warping": True,
                "loop_start": 4,
                "loop_end": 36,
                "start_marker": 4,
                "arrangement_positions": [0, 32, 64, 96, 128],
            }
        )
    manifest = {
        "purpose": "disposable Arrangement acceptance only; synthetic tones, not real stems",
        "tempo": tempo,
        "time_signature": [4, 4],
        "source_duration_beats": beats,
        "audio_tracks": assets,
        "midi_track": {
            "name": "Acceptance MIDI",
            "length": 32,
            "notes": [
                {
                    "pitch": 60,
                    "start_time": 0,
                    "duration": 1,
                    "velocity": 90,
                    "probability": 0.75,
                    "velocity_deviation": 5,
                    "release_velocity": 50,
                },
                {"pitch": 64, "start_time": 4, "duration": 2, "velocity": 100},
            ],
        },
        "target_sections": {
            "break": [64, 96],
            "build": [96, 128],
            "return": 128,
            "pre_return_gap": [127, 128],
        },
        "build_instructions": [
            "Open a new disposable Set; save a separate recovery copy.",
            "Create the four named audio tracks. Keep playback stopped.",
            "For each asset, let the agent create a temporary Session clip and enable warping.",
            "Set source loop and start markers from this manifest.",
            "Duplicate to each Arrangement position.",
            "Remove the temporary Session source clips after native Arrangement copies exist.",
            "Create the named MIDI track and load a stock instrument for audio output.",
            "Let the agent create the MIDI source clip and add these notes.",
            "Copy to Arrangement at beat zero, then remove its Session source.",
            "Inspect actual Arrangement bounds; each audio copy must occupy 32 beats.",
            "Save the fixture Set and keep an unchanged recovery copy before acceptance edits.",
        ],
    }
    (output / "fixture.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New or empty asset directory")
    args = parser.parse_args()
    create_fixture(args.output)
    print(str(Path(args.output).expanduser().resolve() / "fixture.json"))
