"""Read-only, bounded snapshots of Arrangement lanes in saved Live Sets."""

import gzip
import hashlib
import io
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from ._als_xml import MAX_XML_BYTES

MAX_COMPRESSED_BYTES = 64 * 1024 * 1024


class SnapshotError(ValueError):
    pass


def _read_once(path):
    with path.open("rb") as stream:
        before = path.stat()
        data = stream.read(MAX_COMPRESSED_BYTES + 1)
        after = path.stat()
    def identity(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after):
        raise SnapshotError("Set file changed during inspection; save and retry")
    if len(data) > MAX_COMPRESSED_BYTES:
        raise SnapshotError("Compressed Set exceeds the 64 MiB inspection limit")
    return data, identity(after)


def read_snapshot(path, expected_sha256=None, settle_seconds=0.1):
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.lower() != ".als" or not path.is_file():
        raise SnapshotError("Pass an existing .als file")
    first, identity = _read_once(path)
    time.sleep(settle_seconds)
    data, second_identity = _read_once(path)
    if first != data or identity != second_identity:
        raise SnapshotError("Set file is not stable; save and retry")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise SnapshotError("Saved Set differs from the expected snapshot; inspect again")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        xml = stream.read(MAX_XML_BYTES + 1)
    if len(xml) > MAX_XML_BYTES:
        raise SnapshotError("Expanded Set exceeds the XML inspection limit")
    # No entity declarations are necessary in Live Set XML.
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise SnapshotError("XML declarations with external or custom entities are not supported")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SnapshotError("Saved Set XML is invalid: " + str(exc)) from exc
    live_set = root.find("LiveSet")
    if root.tag != "Ableton" or live_set is None:
        raise SnapshotError("File does not contain an Ableton LiveSet")
    tracks = list(live_set.findall("Tracks/*"))
    tracks += [e for e in live_set if e.tag in ("MasterTrack", "MainTrack")]
    result = []
    for track in tracks:
        # Deliberately exclude Session clip envelopes and nested modulation.
        lanes = track.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope")
        name = track.find("Name/EffectiveName")
        addresses = {}
        for node in track.iter():
            target = node.find("AutomationTarget")
            if target is not None and target.get("Id") is not None:
                addresses.setdefault(target.get("Id"), []).append(node.tag)
        for lane in lanes:
            target = lane.find("EnvelopeTarget/PointeeId")
            target_id = target.get("Value") if target is not None else None
            events = lane.find("Automation/Events")
            result.append({
                "track_id": track.get("Id"), "track_type": track.tag,
                "track_name": name.get("Value") if name is not None else None,
                "envelope_id": lane.get("Id"), "target_id": target_id,
                "parameter_candidates": addresses.get(target_id, []),
                "events": [
                    {"type": event.tag, "attributes": dict(event.attrib)}
                    for event in events
                ] if events is not None else [],
                "raw_envelope_xml": ET.tostring(lane, encoding="unicode"),
            })
    return {
        "path": str(path), "sha256": digest, "size": len(data),
        "mtime_ns": identity[3], "creator": root.get("Creator"),
        "scope": "saved_arrangement_automation_only",
        "time_coordinates": "absolute Arrangement beats; raw Live sentinel times retained",
        "interpolation": "raw event attributes and XML retained; not interpreted",
        "live_state_equivalence": "not_verified",
        "lanes": result,
    }


def save_then_inspect(path, save, active_set_confirmed=False,
                      manual_save_confirmed=False, expected_sha256=None):
    """Never treat an API success response alone as proof of a disk save."""
    if not active_set_confirmed:
        return {"status": "needs_active_set_confirmation",
                "message": "Confirm this exact path belongs to the open Set before saving"}
    before = read_snapshot(path)
    if manual_save_confirmed:
        before.update(status="snapshot_ready", verification="user_confirmed_manual_save")
        if expected_sha256 is not None and before["sha256"] != expected_sha256:
            raise SnapshotError("Saved Set differs from the expected snapshot; inspect again")
        return before
    save_result = save()
    if not isinstance(save_result, dict) or not save_result.get("available") or not save_result.get("saved"):
        return {"status": "needs_manual_save", "save_result": save_result,
                "message": "Save this Set in Live, then retry with manual_save_confirmed=true"}
    # A save can finish asynchronously. Poll for a stable, changed file.
    for _ in range(5):
        after = read_snapshot(path)
        if (after["sha256"], after["mtime_ns"]) != (before["sha256"], before["mtime_ns"]):
            if expected_sha256 is not None and after["sha256"] != expected_sha256:
                raise SnapshotError("Saved Set differs from the expected snapshot; inspect again")
            after.update(status="snapshot_ready", verification="api_success_and_file_change",
                         save_result=save_result)
            return after
    return {"status": "needs_manual_save", "save_result": save_result,
            "message": "No file update verified. The Set can already be saved; confirm a manual save before inspection"}
