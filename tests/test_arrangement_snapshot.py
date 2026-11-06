import gzip
import hashlib

import pytest

from ableton_live_mcp.tools import _arrangement_snapshot as snapshots

XML = b'''<Ableton Creator="Live 12"><LiveSet><Tracks><MidiTrack Id="7">
<Name><EffectiveName Value="Bass"/></Name>
<DeviceChain><Mixer><Volume><AutomationTarget Id="42"/></Volume></Mixer></DeviceChain>
<AutomationEnvelopes><Envelopes><AutomationEnvelope Id="3">
<EnvelopeTarget><PointeeId Value="42"/></EnvelopeTarget><Automation><Events>
<FloatEvent Time="-63072000" Value="0.5"/>
<FloatEvent Time="64" Value="0.8" CurveControl1X="0.2"/>
</Events></Automation></AutomationEnvelope></Envelopes></AutomationEnvelopes>
<ClipSlots><ClipSlot><Clip><MidiClip><AutomationEnvelopes><Envelopes>
<AutomationEnvelope Id="99"/></Envelopes></AutomationEnvelopes>
</MidiClip></Clip></ClipSlot></ClipSlots>
</MidiTrack></Tracks></LiveSet></Ableton>'''


@pytest.fixture
def saved_set(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots.time, "sleep", lambda _: None)
    path = tmp_path / "test.als"
    path.write_bytes(gzip.compress(XML))
    return path


def test_exact_snapshot_excludes_session_envelopes(saved_set):
    result = snapshots.read_snapshot(saved_set)
    assert result["sha256"] == hashlib.sha256(saved_set.read_bytes()).hexdigest()
    assert result["live_state_equivalence"] == "not_verified"
    assert len(result["lanes"]) == 1
    lane = result["lanes"][0]
    assert lane["target_id"] == "42"
    assert lane["parameter_candidates"] == ["Volume"]
    assert lane["events"][0]["attributes"]["Time"] == "-63072000"
    assert lane["events"][1]["attributes"]["CurveControl1X"] == "0.2"


def test_stale_digest_rejected(saved_set):
    with pytest.raises(snapshots.SnapshotError, match="expected snapshot"):
        snapshots.read_snapshot(saved_set, expected_sha256="wrong")


def test_no_save_without_active_set_confirmation(saved_set):
    def forbidden():
        pytest.fail("Must not save without path confirmation")
    result = snapshots.save_then_inspect(saved_set, forbidden)
    assert result["status"] == "needs_active_set_confirmation"


@pytest.mark.parametrize("reply", [{"available": False}, {"available": True, "saved": False}, None])
def test_unavailable_or_failed_save_requires_manual_save(saved_set, reply):
    result = snapshots.save_then_inspect(saved_set, lambda: reply, True)
    assert result["status"] == "needs_manual_save"
    assert "lanes" not in result


def test_success_without_file_update_is_not_verified(saved_set):
    result = snapshots.save_then_inspect(
        saved_set, lambda: {"available": True, "saved": True}, True,
    )
    assert result["status"] == "needs_manual_save"


def test_success_and_changed_file_returns_snapshot(saved_set):
    def save():
        saved_set.write_bytes(gzip.compress(XML.replace(b'Value="0.8"', b'Value="0.9"')))
        return {"available": True, "saved": True}
    result = snapshots.save_then_inspect(saved_set, save, True)
    assert result["status"] == "snapshot_ready"
    assert result["verification"] == "api_success_and_file_change"
    assert result["lanes"][0]["events"][1]["attributes"]["Value"] == "0.9"


def test_manual_confirmation_does_not_attempt_api_save(saved_set):
    def forbidden():
        pytest.fail("Manual confirmation must not save again")
    result = snapshots.save_then_inspect(saved_set, forbidden, True, True)
    assert result["verification"] == "user_confirmed_manual_save"


def test_file_changes_during_snapshot_rejected(saved_set, monkeypatch):
    def change(_):
        saved_set.write_bytes(gzip.compress(XML + b"\n"))
    monkeypatch.setattr(snapshots.time, "sleep", change)
    with pytest.raises(snapshots.SnapshotError, match="not stable"):
        snapshots.read_snapshot(saved_set)


def test_wrong_root_rejected(saved_set):
    saved_set.write_bytes(gzip.compress(b"<Other/>"))
    with pytest.raises(snapshots.SnapshotError, match="LiveSet"):
        snapshots.read_snapshot(saved_set)


def test_manual_save_stale_digest_rejected(saved_set):
    with pytest.raises(snapshots.SnapshotError, match="expected snapshot"):
        snapshots.save_then_inspect(
            saved_set, lambda: None, True, True, expected_sha256="old",
        )


def test_compressed_size_limit(saved_set, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_COMPRESSED_BYTES", 1)
    with pytest.raises(snapshots.SnapshotError, match="Compressed Set"):
        snapshots.read_snapshot(saved_set)


def test_expanded_size_limit(saved_set, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_XML_BYTES", 1)
    with pytest.raises(snapshots.SnapshotError, match="Expanded Set"):
        snapshots.read_snapshot(saved_set)


def test_invalid_xml_rejected(saved_set):
    saved_set.write_bytes(gzip.compress(b"<Ableton>"))
    with pytest.raises(snapshots.SnapshotError, match="XML is invalid"):
        snapshots.read_snapshot(saved_set)


def test_entities_rejected(saved_set):
    saved_set.write_bytes(gzip.compress(b'<!DOCTYPE Ableton [<!ENTITY x "foo">]>' + XML))
    with pytest.raises(snapshots.SnapshotError, match="entities"):
        snapshots.read_snapshot(saved_set)
