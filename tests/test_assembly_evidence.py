import numpy as np

from car2lego3d.assembly_evidence import files_current, module_report_current, module_task_current, module_tasks_current, snapshot_files
from car2lego3d.assembly_requirements import identity
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json


def test_missing_files_and_changed_bytes_invalidate_evidence(tmp_path):
    present, absent = tmp_path / "part.dat", tmp_path / "missing.conn"
    present.write_bytes(b"original")
    bound = snapshot_files([present, absent])
    assert files_current(bound)
    absent.write_bytes(b"new connectivity")
    assert not files_current(bound)
    assert not files_current(None)


def test_same_revision_cannot_hide_graph_or_binding_changes(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.call("edit", dict(revision=0, operations=[dict(op="add", part="3001.dat", matrix=np.eye(4).tolist())]))
    report = dict(graph_sha256=identity(tools.assembly.graph()), bindings_sha256=identity({}),
        evidence_files=snapshot_files([tmp_path / "state.json"]))
    assert module_report_current(tmp_path, report)
    tools.assembly.sections[tools.assembly.root][1]["line"] = "0 changed source"
    tools.module_bindings = dict(chassis={"changed": True})
    tools.save()
    # Even a re-bound state-file hash cannot impersonate the original graph/bindings.
    report["evidence_files"] = snapshot_files([tmp_path / "state.json"])
    assert not module_report_current(tmp_path, report)


def test_native_collider_changes_are_read_without_restarting_catalog(tmp_path):
    from car2lego3d.assembly_catalog import NativeCatalog
    path = tmp_path / "collider/part.col"
    path.parent.mkdir()
    def row(x):
        return "9 0 1 0 0 0 1 0 0 0 1 " + str(x) + " 0 0 1 1 1\n"
    path.write_text(row(0))
    catalog = NativeCatalog(tmp_path)
    assert catalog.boxes("part.dat")[0][0][0] == 0
    path.write_text(row(5))
    assert catalog.boxes("part.dat")[0][0][0] == 5


def test_review_images_and_resolved_geometry_are_bound_to_module_evidence(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    image = tmp_path / "inspection/front.png"
    image.parent.mkdir()
    image.write_bytes(b"reviewed pixels")
    from car2lego3d.common import digest
    write_json(tmp_path / "module-role-review.json", dict(evidence=[
        dict(file="inspection/front.png", sha256=digest(image))], native_evidence_files=[]))
    report = tools.tool_module_progress()
    assert files_current(report["evidence_files"])
    assert str(image) in {item["file"] for item in report["evidence_files"]}
    image.write_bytes(b"different pixels")
    assert not files_current(report["evidence_files"])


def test_task_checks_must_cover_every_module_and_the_final_surroundings(tmp_path):
    from copy import deepcopy
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.save()
    requirements = dict(chassis=dict(roles=["structure"]), cabin=dict(roles=["seats"]))
    proof = dict(status="pass", graph_sha256=identity(tools.assembly.graph()), bindings_sha256=identity({}),
        candidates=dict(status="pass"), native_evidence=[])
    tasks = {name: dict(id=name, dependencies=[] if name == "chassis" else ["chassis"],
        requirement=requirement, status="assembled", measurement=deepcopy(proof))
        for name, requirement in requirements.items()}
    schedule = dict(schema="vehicle-assembly-schedule.v2", contract=dict(mission=dict(vehicle_contract=dict(modules=requirements))),
        complete=2, required=2, tasks=tasks)
    assert module_tasks_current(tmp_path, schedule)
    for fault in ("neighbor", "missing", "pending", "candidates", "binding", "cycle"):
        stale = deepcopy(schedule)
        if fault == "neighbor":
            stale["tasks"]["chassis"]["measurement"]["graph_sha256"] = "earlier-surroundings"
        elif fault == "missing":
            stale["tasks"].pop("cabin")
        elif fault == "pending":
            stale["tasks"]["cabin"]["status"] = "pending"
        elif fault == "candidates":
            stale["tasks"]["cabin"]["measurement"]["candidates"] = None
        elif fault == "binding":
            stale["tasks"]["cabin"]["measurement"]["bindings_sha256"] = "earlier-bindings"
        else:
            stale["tasks"]["chassis"]["dependencies"] = ["cabin"]
        assert not module_tasks_current(tmp_path, stale), fault


def test_single_task_evidence_is_bound_to_module_signature_and_current_graph(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    added = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="chassis.ldr", alias="chassis", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@chassis", part="3001.dat", alias="chassis_part", matrix=np.eye(4).tolist())]))
    aliases = added["result"]["aliases"]
    binding = dict(roots=[aliases["chassis"]], roles={"structure": [aliases["chassis_part"]]})
    bindings = {"chassis": binding}
    tools.module_bindings = bindings
    graph = tools.assembly.graph()
    from car2lego3d.assembly_mounts import owned_members, semantic_node
    members = owned_members(graph, bindings, "chassis")
    signature = identity(dict(binding=binding,
        instances=[semantic_node(node) for node in graph["instances"] if node["id"] in members],
        joints={key: value for key, value in graph["joints"].items() if key in members}))
    task = dict(id="chassis", measurement=dict(status="pass", signature=signature,
        graph_sha256=identity(graph), bindings_sha256=identity(bindings), native_evidence=[]))
    assert module_task_current(graph, bindings, task)
    moved = tools.assembly.graph()
    moved["instances"][0]["local"][0][3] += 1
    assert not module_task_current(moved, bindings, task)


def test_v1_schedule_remains_compatible_and_unknown_versions_are_rejected(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.save()
    requirements = {"chassis": {"roles": ["structure"]}}
    proof = dict(status="pass", graph_sha256=identity(tools.assembly.graph()), bindings_sha256=identity({}),
        candidates=dict(status="pass"), native_evidence=[])
    schedule = dict(schema="vehicle-assembly-schedule.v1", contract=dict(mission=dict(vehicle_contract=dict(modules=requirements))),
        complete=1, required=1, tasks={"chassis": dict(id="chassis", dependencies=[], requirement=requirements["chassis"],
            status="assembled", measurement=proof)})
    assert module_tasks_current(tmp_path, schedule)
    schedule["schema"] = "vehicle-assembly-schedule.v3"
    assert not module_tasks_current(tmp_path, schedule)
