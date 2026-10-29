"""Compare real, reproducible module trials instead of planner descriptions."""
import json

import numpy as np

from .assembly_evidence import files_current
from .assembly_requirements import descendants, identity
from .common import digest, write_json


def structure(tools, module):
    from .assembly_mounts import owned_members
    graph = tools.assembly.graph()
    binding = tools.module_bindings[module]
    members = owned_members(graph, tools.module_bindings, module)
    nodes = {n["id"]: n for n in graph["instances"]}
    frame = np.linalg.inv(np.asarray(nodes[binding["roots"][0]]["world"]))
    parts = sorted((dict(part=n["part"], matrix=np.round(frame @ n["world"], 7).tolist())
        for n in graph["instances"] if n["id"] in members and n["kind"] == "part"), key=identity)
    return identity(parts)


def articulation(tools, module):
    """Identify motion geometry separately from the counted part structure."""
    from .assembly_mounts import owned_members
    graph = tools.assembly.graph()
    members = owned_members(graph, tools.module_bindings, module)
    nodes = {n["id"]: n for n in graph["instances"]}
    frame = np.linalg.inv(np.asarray(nodes[tools.module_bindings[module]["roots"][0]]["world"]))
    joints = []
    for key, joint in graph["joints"].items():
        if key not in members:
            continue
        parent = nodes[key]["parent"]
        transform = frame @ (np.asarray(nodes[parent]["world"]) if parent != "root" else np.eye(4))
        joints.append(dict(
            frame=np.round(frame @ nodes[key]["world"], 7).tolist(),
            axis=np.round(transform[:3, :3] @ joint["axis"], 7).tolist(),
            origin=np.round((transform @ [*joint.get("origin", [0, 0, 0]), 1])[:3], 7).tolist(),
            type=joint.get("type", "revolute"), purpose=joint.get("purpose"), limits=joint["limits"]))
    return identity(sorted(joints, key=identity))


def candidate_id(record):
    return record.get("structure_id", record["id"])


def snapshot_articulation(path, module):
    from types import SimpleNamespace
    from .assembly_graph import Assembly
    state = json.loads(path.read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    return articulation(SimpleNamespace(assembly=assembly, module_bindings=state["module_bindings"]), module)


def surroundings(tools, module):
    from .assembly_mounts import owned_members, semantic_node
    graph = tools.assembly.graph()
    members = owned_members(graph, tools.module_bindings, module)
    return identity(dict(instances=[semantic_node(n) for n in graph["instances"] if n["id"] not in members],
        joints={k: v for k, v in graph["joints"].items() if k not in members},
        bindings={k: v for k, v in tools.module_bindings.items() if k != module}))


def folder(tools, module):
    if module not in (tools.vehicle_contract or {}).get("modules", {}):
        raise ValueError("Candidate requires a host-required module")
    return tools.directory / "module-candidates" / module


def capture(tools, revision, module, label):
    if revision != tools.assembly.revision:
        raise ValueError("Stale assembly revision")
    from .assembly_schedule import module_check
    measurement = module_check(tools, module, compare=False)
    if not measurement.get("signature"):
        raise ValueError("Bind the actual candidate before measuring it")
    fingerprint, joints = structure(tools, module), articulation(tools, module)
    prior = [r for r in records(tools, module) if candidate_id(r) == fingerprint]
    for record in prior:
        old_path = folder(tools, module) / record["id"] / "state.json"
        previous = record.get("articulation_id") or snapshot_articulation(old_path, module)
        if previous == joints:
            raise ValueError("This structure and articulation were already measured; recoloring or renaming is not a new candidate")
    # A joint repair gets a new immutable snapshot, but not another structural vote.
    identifier = identity(dict(structure=fingerprint, articulation=joints)) if prior else fingerprint
    path = folder(tools, module) / identifier
    if path.exists():
        raise ValueError("This structure was already measured; recoloring or renaming is not a new candidate")
    tools.save()
    write_json(path / "state.json", json.loads((tools.directory / "state.json").read_text(encoding="utf-8")))
    record = dict(schema="assembly-candidate.v2", module=module, id=identifier, label=label,
        structure_id=fingerprint, articulation_id=joints,
        surroundings=surroundings(tools, module), measurement=measurement,
        snapshot_sha256=digest(path / "state.json"), independent_semantics="unverified")
    write_json(path / "candidate.json", record)
    return record


def records(tools, module):
    result = []
    for path in sorted(folder(tools, module).glob("*/candidate.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        snapshot = path.parent / "state.json"
        record["current"] = (snapshot.is_file() and digest(snapshot) == record["snapshot_sha256"]
            and files_current(record["measurement"].get("native_evidence")))
        result.append(record)
    return result


def comparison(tools, module):
    from .assembly_schedule import module_signature
    candidates = records(tools, module)
    current = structure(tools, module)
    valid = [r for r in candidates if r["current"] and r["measurement"].get("status") == "pass"]
    selected = next((r["id"] for r in valid if candidate_id(r) == current
        and r["measurement"].get("signature") == module_signature(tools, module)), None)
    passing = len({candidate_id(r) for r in valid})
    ready = passing >= 3 and selected is not None
    return dict(status="pass" if ready else "unverified", required=3, passing=passing, selected=selected,
        candidates=[dict(id=r["id"], structure_id=candidate_id(r), label=r["label"], status=r["measurement"]["status"], current=r["current"])
            for r in candidates], reason=None if ready else "three_distinct_verified_structures_required")


def restore(tools, revision, module, candidate):
    if revision != tools.assembly.revision:
        raise ValueError("Stale assembly revision")
    record = next((r for r in records(tools, module) if r["id"] == candidate), None)
    if not record or not record["current"]:
        raise ValueError("Candidate snapshot or native evidence is missing or changed")
    if surroundings(tools, module) != record["surroundings"]:
        raise ValueError("Surrounding assembly changed; restoring this trial would discard other module work")
    from .assembly_graph import Assembly
    from .assembly_catalog import NativeCatalog
    state = json.loads((folder(tools, module) / candidate / "state.json").read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = revision + 1, state["joints"]
    tools.assembly, tools.module_bindings = assembly, state["module_bindings"]
    tools.catalog = NativeCatalog(tools.catalog_root, [tools.directory / p for p in state["packages"]])
    return dict(module=module, candidate=candidate, revision=assembly.revision, checks="required")
