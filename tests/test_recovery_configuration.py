"""Recovery opt-out is user configuration, not an LLM-facing tool argument."""

import ast
import os
from pathlib import Path

import pytest
from test_arrangement_engine import preview

pytest_plugins = ["test_arrangement_engine"]


def call(engine, **params):
    plan = preview(engine, operation="duplicate", destination=8)
    return engine.command("execute", {"plan": plan["plan"], **params})


def test_default_still_requires_recovery(engine):
    assert call(engine, authorized=True)["code"] == "authorization_required"
    assert engine.song.undo == []


def test_opt_out_allows_execution_without_file(engine):
    result = call(engine, authorized=True, require_recovery_point=False)
    assert result["status"] == "edited"
    assert result["recovery_point"] is None
    assert result["recovery_point_required"] is False
    assert result["warnings"]


def test_opt_out_does_not_bypass_authorization(engine):
    assert call(engine, require_recovery_point=False)["code"] == "authorization_required"
    assert engine.song.undo == []


def test_opt_out_does_not_bypass_reconstruction_authorization(engine):
    plan = preview(engine, operation="split", beat=2)
    result = engine.command(
        "execute", {"plan": plan["plan"], "authorized": True, "require_recovery_point": False}
    )
    assert result["code"] == "reconstruction_authorization_required"
    assert engine.song.undo == []


def test_supplied_invalid_recovery_is_not_silently_accepted(engine):
    result = call(
        engine,
        authorized=True,
        require_recovery_point=False,
        recovery_point="/missing/recovery.als",
    )
    assert result["code"] == "recovery_required"
    assert engine.song.undo == []


def test_preview_reports_opt_out(engine):
    result = preview(engine, operation="duplicate", destination=8, require_recovery_point=False)
    assert result["recovery_point_required"] is False
    assert "disabled" in result["requires"]


def test_failure_without_file_has_honest_recovery_message(engine):
    engine.song.tracks[0].duplicate_clip_to_arrangement = lambda *args: (_ for _ in ()).throw(
        RuntimeError("failure")
    )
    result = call(engine, authorized=True, require_recovery_point=False)
    assert result["status"] == "partial_failure"
    assert result["recovery_point"] is None
    assert "No saved recovery" in result["recovery"]


@pytest.mark.parametrize(
    "value,required",
    [
        (None, True),
        ("true", True),
        ("false", False),
        (" FALSE ", False),
        ("0", True),
        ("typo", True),
    ],
)
def test_environment_default_and_explicit_opt_out(monkeypatch, value, required):
    if value is None:
        monkeypatch.delenv("ABLETON_REQUIRE_RECOVERY_ALS", raising=False)
    else:
        monkeypatch.setenv("ABLETON_REQUIRE_RECOVERY_ALS", value)
    path = Path(__file__).parents[1] / "ableton_live_mcp/tools/arrangement.py"
    tree = ast.parse(path.read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_require_recovery_point"
    )
    ns = {"os": os}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    assert ns["_require_recovery_point"]() is required


def test_tool_configuration_is_not_a_public_execution_argument():
    path = Path(__file__).parents[1] / "ableton_live_mcp/tools/arrangement.py"
    tree = ast.parse(path.read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "execute_arrangement_edit"
    )
    assert "require_recovery_point" not in [arg.arg for arg in node.args.args]
    assert '"require_recovery_point": _require_recovery_point()' in ast.get_source_segment(
        path.read_text(), node
    )
