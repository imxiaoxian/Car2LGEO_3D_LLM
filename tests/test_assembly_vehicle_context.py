import json

import numpy as np
from PIL import Image
import pytest

from car2lego3d.assembly_agent import AssemblyAgent, wire_json
from car2lego3d.assembly_catalog import NativeCatalog
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.assembly_vehicle_context import prepare_vehicle_context
from car2lego3d.common import digest, write_json
from car2lego3d.assembly_prompt_codec import PORT_FORMAT, VERSION as PORT_ENCODING_VERSION, unpack_payload


def test_native_search_reads_actual_descriptions_without_inventing_evidence(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "3001.dat").write_text("0 Brick 2 x 4\n")
    (parts / "3068b.dat").write_text("0 Tile 2 x 2 with Groove\n")
    catalog = NativeCatalog(tmp_path)
    assert catalog.search("tile groove")["parts"] == [dict(part="3068b.dat", description="Tile 2 x 2 with Groove")]
    assert catalog.search("wheel")["total"] == 0
    assert catalog.search("3001")["geometry_and_connection_status"] == "query_part_required"


def test_create_door_module_keeps_glass_and_panel_in_one_moving_subtree(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    matrix = np.eye(4)
    matrix[:3, 3] = [100, -20, 40]
    created = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="left-door.ldr", matrix=matrix.tolist())]))
    assert created["ok"]
    door = created["result"]["instances"][0]["id"]
    local = np.eye(4)
    local[1, 3] = -24
    assert tools.call("edit", dict(revision=1, operations=[
        dict(op="add", parent=door, part="3001.dat", color=4, matrix=np.eye(4).tolist()),
        dict(op="add", parent=door, part="3002.dat", color=47, matrix=local.tolist()),
        dict(op="joint", instance=door, axis=[0, 0, 1], limits=[0, 70])]))["ok"]
    graph = tools.assembly.graph()
    children = [n for n in graph["instances"] if n["kind"] == "part"]
    assert {n["parent"] for n in children} == {door}
    assert np.allclose(np.array(children[1]["world"])[:3, 3], [100, -44, 40])
    restored = Assembly(tools.assembly.text())
    assert restored.graph()["instances"] == graph["instances"]
    assert graph["joints"][door]["limits"] == [0, 70]
    before = tools.assembly.text()
    assert not tools.call("edit", dict(revision=2, operations=[
        dict(op="module", name="left-door.ldr", matrix=np.eye(4).tolist())]))["ok"]
    assert tools.assembly.text() == before and tools.assembly.revision == 2


def test_rich_context_binds_actual_images_and_exact_bounds(tmp_path):
    views = {}
    for number, name in enumerate(("front", "back", "left", "right")):
        image = tmp_path / (name + ".png")
        Image.new("RGB", (32, 32), (number * 50, 30, 10)).save(image)
        views[name] = dict(file=image.name, sha256=digest(image))
    manifest = tmp_path / "views.json"
    write_json(manifest, dict(schema_version=1, views=views))
    index = dict(node_count=2, nodes={
        "body1": dict(role="body", world_bounds_m=[[0, -1, 0], [4, 1, 1]]),
        "body2": dict(role="body", world_bounds_m=[[1, -.8, .5], [3, .8, 1.3]])})
    context, images = prepare_vehicle_context(tmp_path / "context", index, manifest, {"model": "fixture"})
    assert len(images) == 4
    assert context["roles"]["body"]["world_bounds_m"] == [[0, -1, 0], [4, 1, 1.3]]
    assert context["summary_only"] and context["source_node_count"] == 2

    class Backend:
        name = "codex"
        vehicle_context = context

    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    tools.reference_images = images
    agent = AssemblyAgent(tools, Backend(), {})
    selected = tools.call("view_reference", {"view": "left"})
    assert selected["ok"] and selected["result"]["sha256"] == views["left"]["sha256"]
    payload = agent.context([dict(decision=dict(tool="view_reference"), observation=selected)], 8)
    assert payload["image_inputs"][1]["sha256"] == views["left"]["sha256"]
    assert payload["tool_signatures"]["search_parts"]
    assert "tool_schemas" not in payload and "skill" not in payload
    assert agent.contract["planner_context_profile"] == "vehicle-scoped.v3"
    observation = tools.call("inspect", {})
    history = [dict(step=i, decision=dict(tool="inspect", arguments_json="{}"),
        observation=observation, input_text="recursive previous input" * 100000) for i in range(12)]
    bounded = agent.context(history, 8)
    assert len(bounded["recent_history"]) == 4
    assert len(json.dumps(bounded)) < 40000
    assert "recursive previous input" not in json.dumps(bounded)
    (tmp_path / "left.png").write_bytes(b"changed")
    assert not tools.call("view_reference", {"view": "left"})["ok"]


def test_module_context_supplies_full_task_and_keeps_unrelated_geometry_addressable(tmp_path):
    context = dict(schema="assembly-vehicle-context.v1", source_node_count=1000,
        coordinates=dict(source="metres", target="LDU"),
        roles={"tyre": dict(world_bounds_m=[[0, 0, 0], [1, 1, 1]]),
            "seat": dict(example_ids=["seat"] * 10000)},
        specifications=dict(model="Countach", measurements=dict(length=4140)),
        semantic_geometry_available="semantic:/nodes/<index>", reference_views={},
        contact_sheet=dict(file="fixture.png", sha256="fixture"), instructions="Preserve native parts")
    task = dict(id="wheel_front_left", dependencies=["chassis"],
        requirement=dict(roles=["tyre", "rim", "hub"], joint_purpose="rolling"),
        prerequisite_bindings=dict(chassis=dict(roots=["frame"], roles=dict(structure=["rail"]))))
    backend = type("Backend", (), dict(name="codex", vehicle_context=context))()
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    agent = AssemblyAgent(tools, backend, dict(module_task=task), finish_check=lambda: {})
    payload = agent.context([], 20)
    assert payload["module_task"]["requirement"] == task["requirement"]
    assert payload["module_task"]["prerequisite_bindings"] == task["prerequisite_bindings"]
    assert set(payload["vehicle"]["roles"]) == {"tyre"}
    assert len(wire_json(payload)) < 12000
    assert tools.call("context", dict(resource="vehicle_context:/roles/seat/example_ids/9999"))["result"]["text"] == "seat"
    assert tools.call("context", dict(resource="vehicle_context:/specifications/measurements/length"))["result"]["text"] == "4140"
    assert context["roles"]["seat"]["example_ids"] == ["seat"] * 10000


def test_module_payload_attaches_only_scoped_sheet_and_originals_remain_queryable(tmp_path):
    from car2lego3d.reference_plan import reference_plan

    shots = {}
    for number, shot in enumerate(reference_plan("Countach")):
        image = tmp_path / (shot.id + ".png")
        Image.new("RGB", (40, 80), (number, 50, 10)).save(image)
        shots[shot.id] = dict(shot.record(), file=image.name, sha256=digest(image))
    for record in shots.values():
        record["parents"] = {parent: shots[parent]["sha256"] for parent in record["parents"]}
    manifest = tmp_path / "views.json"
    write_json(manifest, dict(schema_version=2, shots=shots,
        expected_shots=[shot.record() for shot in reference_plan("Countach")],
        views={view: shots[view] for view in ("front", "back", "left", "right")}))
    context, images = prepare_vehicle_context(tmp_path / "context", dict(node_count=0, nodes={}),
        manifest, {}, modules=["door_right", "steering"])
    backend = type("Backend", (), dict(name="codex", vehicle_context=context))()
    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    tools.reference_images = images
    agent = AssemblyAgent(tools, backend, dict(module_task=dict(id="door_right")), finish_check=lambda: {})
    payload = agent.context([], 20)
    sheet = context["module_reference_sheets"]["door_right"]
    assert payload["image_inputs"] == [{key: sheet[key] for key in ("file", "sha256")}]
    assert payload["image_inputs"][0] != context["contact_sheet"]
    assert "door_right_open" in payload["vehicle"]["reference_views"]
    assert "door_left_open" not in payload["vehicle"]["reference_views"]
    assert "door_left_open" in payload["vehicle"]["reference_scope"]["omitted_views"]
    assert tools.call("view_reference", dict(view="door_left_open"))["ok"]
    assert agent.contract["planner_context"]["vehicle_context_sha256"]


def test_shared_text_context_is_exactly_equal_for_all_planners(tmp_path):
    mission = dict(request=dict(brand="Lamborghini", model="Countach"),
        comparison_context_profile="shared-text", target_profile=dict(features=["scissor_doors"]))
    payloads, contracts = [], []
    for name in ("codex", "local_base", "local_finetuned"):
        backend = type("Backend", (), {"name": name})()
        tools = AssemblyTools(tmp_path / name, tmp_path / "native")
        agent = AssemblyAgent(tools, backend, mission)
        payloads.append(wire_json(agent.context([], 100)))
        contracts.append(agent.input_hash)
    assert len(set(payloads)) == len(set(contracts)) == 1
    assert '"image_inputs"' not in payloads[0]


def test_vehicle_workflow_is_inline_and_context_history_points_to_original_data(tmp_path):
    backend = type("Backend", (), dict(name="codex", vehicle_context=dict(
        contact_sheet=dict(file="fixture.png", sha256="fixture"))))()
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    agent = AssemblyAgent(tools, backend, {})
    search = tools.call("search_parts", dict(query="wheel"))
    tools.context_resources["observations"]["0"] = search
    page = tools.call("context", dict(resource="observations", pointer="/0/result", limit=30))
    history = [dict(step=0, decision=dict(tool="search_parts"), observation=search),
               dict(step=1, decision=dict(tool="context"), observation=page)]
    payload = agent.context(history, 20)
    assert "read guide:/start if unread" not in payload["instruction"]
    assert "vehicle body categories" in payload["tool_usage"]["retrieve"]
    assert any("candidate" in item and "bind_module" in item for item in payload["workflow"])
    assert payload["known_evidence"]["part_searches"]["0"] == search["result"]
    location = payload["recent_history"][-1]["observation"]
    assert location["resource"] == "observations" and location["pointer"] == "/0/result"
    assert location["offset"] == 0 and location["next_offset"] == 30
    assert "full_result_requires_context" not in location
    assert payload["last"]["data"] == page["result"]


def test_stateless_vehicle_planners_receive_all_collision_pairs_after_multiple_reads(tmp_path):
    pairs = [dict(a=f"root/plate{i}", b="root/rack", evidence="native-collider-OBB", status="fail")
             for i in range(3)]
    payloads = []
    for name in ("codex", "local_base", "local_finetuned"):
        backend = type("Backend", (), dict(name=name, vehicle_context=dict(
            contact_sheet=dict(file="fixture.png", sha256="fixture"))))()
        tools = AssemblyTools(tmp_path / name, tmp_path / "native")
        agent = AssemblyAgent(tools, backend, {})
        report = dict(revision=0, status="fail", components=[["root/rack"]], collisions=pairs, unknown=[])
        observations = tools.context_resources["observations"]
        observations["0"] = dict(tool="check", ok=True, revision_before=0, revision_after=0, result=report)
        history = []
        for i in range(3):
            arguments = dict(resource="observations", pointer=f"/0/result/collisions/{i}")
            observation = tools.call("context", arguments)
            assert observation["ok"]
            observations[str(i+1)] = observation
            history.append(dict(step=i+1, decision=dict(tool="context", arguments_json=wire_json(arguments)),
                                observation=observation))
        payload = unpack_payload(agent.context(history, 20))
        check = payload["known_evidence"]["diagnostics"]["check"]
        assert check["current"] and check["collision_pairs"] == pairs
        pages = payload["known_evidence"]["context_pages"]
        assert [json.loads(page["text"]) for page in pages] == pairs
        assert payload["last"]["data"]["pointer"] == "/0/result/collisions/2"
        payloads.append(payload)
    assert payloads[0] == payloads[1] == payloads[2]


def test_vehicle_planners_receive_same_lossless_ports_and_raw_resource_paths(tmp_path):
    ports = [dict(id=str(i), kind="hole", position=[i * 20, 0, 0], direction=[0, 0, 1],
        length=20, start_capped=False, end_capped=False, semantic="pin_socket") for i in range(100)]
    result = dict(part="3703.dat", ports=ports, status="unverified", errors=["missing collider"])
    observation = dict(ok=True, tool="query_part", result=result)
    payloads = []
    for name in ("codex", "local_base", "local_finetuned"):
        backend = type("Backend", (), dict(name=name, vehicle_context=dict(
            contact_sheet=dict(file="fixture.png", sha256="fixture"))))()
        tools = AssemblyTools(tmp_path / name, tmp_path / "native")
        agent = AssemblyAgent(tools, backend, {})
        tools.context_resources["observations"]["0"] = observation
        payload = agent.context([dict(step=0, decision=dict(tool="query_part"), observation=observation)], 20)
        payloads.append(wire_json(payload))
        assert agent.contract["planner_context"]["port_encoding"] == PORT_ENCODING_VERSION
        table = payload["known_evidence"]["parts"]["3703.dat"]["ports"]
        assert table["format"] == PORT_FORMAT and table["count"] == 100
        restored = unpack_payload(payload)
        assert restored["known_evidence"]["parts"]["3703.dat"] == dict(step="0", **result)
        assert restored["state"] == payload["state"]
        assert tools.context_resources["observations"]["0"] == observation
        raw = tools.call("context", dict(resource="observations:/0/result/ports/99/id"))
        assert raw["result"]["text"] == "99"
        assert len(wire_json(payload)) < len(wire_json(restored))
    assert len(set(payloads)) == 1


def test_encoded_inputs_and_trace_remain_identical_across_retry(tmp_path):
    class Backend:
        name = "codex"
        vehicle_context = dict(contact_sheet=dict(file="fixture.png", sha256="fixture"))

        def propose(self, payload, step):
            assert unpack_payload(payload)["state"]["revision"] == 0
            return dict(tool="remember", arguments_json='{"text":"resume fixture"}', reason="trace fixture")

    tools = AssemblyTools(tmp_path, tmp_path / "native")
    agent = AssemblyAgent(tools, Backend(), {}, max_steps=1, finalize=False)
    agent.run()
    resumed = AssemblyAgent(AssemblyTools(tmp_path, tmp_path / "native"), Backend(), {}, max_steps=2, finalize=False)
    resumed.run(retry=True)
    records = [json.loads(row) for row in (tmp_path / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    for record in records:
        saved = json.loads((tmp_path / record["input_file"]).read_text(encoding="utf-8"))
        assert record["input_sha256"] == digest(tmp_path / record["input_file"])
        assert record["input_text"] == wire_json(saved)
        assert unpack_payload(saved)["remaining_steps"] == 1
