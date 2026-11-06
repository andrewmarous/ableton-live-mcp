"""Connection-scoped Arrangement references. No Live imports or mutations."""
import uuid


class StaleArrangementReference(ValueError):
    """The referenced object no longer belongs to the current Set."""


class ArrangementReferences:
    """Hold object references, never indices or Python object IDs.

    Create a new registry after a Set change or Remote Script reconnect.
    Unrelated track/clip edits do not invalidate surviving objects.
    """

    def __init__(self):
        self._entries = {}

    def register(self, track, clip):
        for handle, (existing_track, existing_clip) in self._entries.items():
            if existing_track == track and existing_clip == clip:
                return handle
        handle = uuid.uuid4().hex
        self._entries[handle] = (track, clip)
        return handle

    def resolve(self, song, handle):
        try:
            track, clip = self._entries[handle]
            if track not in song.tracks or clip not in track.arrangement_clips:
                raise StaleArrangementReference("Arrangement reference is stale; inspect again")
            return track, clip
        except (KeyError, RuntimeError, ReferenceError):
            raise StaleArrangementReference("Arrangement reference is stale; inspect again")
