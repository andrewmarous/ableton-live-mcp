"""Shared transport for native creator controls."""

import json

from ..connection import get_ableton_connection


def creator_call(command, **kwargs):
    return json.dumps(
        get_ableton_connection().send_command(
            command, {key: value for key, value in kwargs.items() if value is not None}
        )
    )
