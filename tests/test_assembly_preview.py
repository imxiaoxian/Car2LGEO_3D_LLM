"""Current native render evidence must reach the planner without stale images."""
import json
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner
from car2lego3d.assembly_evidence import snapshot_files
from car2lego3d.assembly_requirements import identity
from car2lego3d.assembly_prompt_codec import unpack_payload
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def test_preview_requires_completely_resolved_native_geometry(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    monkeypatch.setattr(tools, "tool_render", lambda: dict(status="unverified", missing=["fixture"]))
    result = tools.call("preview", {})
    assert not result["ok"] and "complete native" in result["error"]
    assert not (tmp_path / "preview.json").exists()


def test_only_current_hash_bound_preview_is_attached_to_vision_planner(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    assert tools.call("edit", dict(revision=0, operations=[dict(op="add", part="3001.dat", matrix=np.eye(4).tolist())]))["ok"]
    image = tmp_path / "contact-sheet.png"
    image.write_bytes(b"fixture-image-identity")
    record = dict(revision=1, graph_sha256=identity(tools.assembly.graph()),
        image=dict(file=str(image), sha256=digest(image)), evidence_files=snapshot_files([image]))
    write_json(tmp_path / "preview.json", record)
    agent = AssemblyAgent(tools, CodexPlanner(tmp_path / "planner", executable="fixture-cli"), {})
    payload = agent.context([], 20)
    assert payload["actual_preview"]["current"]
    assert payload["image_inputs"][0]["sha256"] == digest(image)
    assert "ACTUAL" in payload["image_inputs"][0]["label"]
    image.write_bytes(b"changed")
    stale = agent.context([], 20)
    assert not stale["actual_preview"]["current"] and "image_inputs" not in stale
    image.write_bytes(b"fixture-image-identity")
    node = tools.assembly.graph()["instances"][0]["id"]
    assert tools.call("edit", dict(revision=1, operations=[dict(op="color", instance=node, color=1)]))["ok"]
    stale = agent.context([], 20)
    assert not stale["actual_preview"]["current"] and "image_inputs" not in stale


def test_text_backend_records_preview_without_claiming_pixel_access(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    image = tmp_path / "contact-sheet.png"
    image.write_bytes(b"fixture")
    write_json(tmp_path / "preview.json", dict(revision=0, graph_sha256=identity(tools.assembly.graph()),
        image=dict(file=str(image), sha256=digest(image)), evidence_files=snapshot_files([image])))
    backend = type("TextBackend", (), {"name": "local_finetuned"})()
    payload = AssemblyAgent(tools, backend, {}).context([], 20)
    assert payload["actual_preview"]["current"]
    assert payload["actual_preview"]["image_transport"] == "text_paths_only"
    assert "image_inputs" not in payload


def test_object_context_retains_task_ports_and_real_aliases_without_vehicle_rules(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    backend = CodexPlanner(tmp_path / "planner", executable="fixture-cli")
    request = dict(goal="Build a native object module and inspect its actual geometry. " * 6,
        parts=["fixture.dat"])
    agent = AssemblyAgent(tools, backend, dict(request=request))
    part = dict(part="fixture.dat", ports=[dict(id="exact-7", position=[3, 4, 5])], status="pass")
    tools.context_resources["observations"].update({
        "0": dict(tool="query_part", ok=True, revision_after=0, result=part),
        "1": dict(tool="build", ok=True, revision_after=0,
            result=dict(aliases=dict(object="root/real-object")))})
    payload = unpack_payload(agent.context([], 20))
    assert payload["task"] == request
    assert payload["known_evidence"]["parts"]["fixture.dat"]["ports"] == part["ports"]
    assert payload["action_progress"]["mutations"][0]["aliases"]["object"] == "root/real-object"
    assert "attach" in payload["build_operation_signatures"]
    assert "vehicle" not in payload and "workflow" not in payload
    assert agent.contract["planner_context"]["profile"] == "object-scoped.v1"


def test_explicit_shared_text_comparison_keeps_compact_contract(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    backend = CodexPlanner(tmp_path / "planner", executable="fixture-cli")
    agent = AssemblyAgent(tools, backend, dict(comparison_context_profile="shared-text"))
    payload = agent.context([], 20)
    assert agent.contract["planner_context"]["profile"] == "compact-navigable.v1"
    assert "known_evidence" not in payload and "build_operation_signatures" not in payload
