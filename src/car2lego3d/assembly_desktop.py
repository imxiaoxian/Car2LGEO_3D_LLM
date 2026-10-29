"""Project website compatibility surface backed by the shared assembly graph."""
from pathlib import Path
import csv
import shutil
import numpy as np

from .common import write_json


def preview_hierarchy(graph):
    """Convert parent-local joint frames into the viewer's global LDraw frame."""
    nodes = {node["id"]: node for node in graph["instances"]}
    modules = [dict(id="root", parent=None, depends_on=[])]
    modules.extend(dict(id=node["id"], parent=node["parent"], depends_on=[])
                   for node in nodes.values() if node["kind"] == "module")
    joints = []
    for identity, joint in graph["joints"].items():
        parent = nodes[identity]["parent"]
        frame = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
        pivot = frame @ np.r_[joint.get("origin", [0, 0, 0]), 1]
        axis = frame[:3, :3] @ np.asarray(joint["axis"])
        joints.append(dict(id=identity, child=identity, parent=parent,
            kind=joint.get("type", "revolute"), axis=axis.tolist(), pivot=pivot[:3].tolist(),
            limits=joint["limits"], implemented=True, status="unverified", capability="assembly_joint"))
    return modules, joints


def publish(tools, artifacts):
    from .assembly_materials import palette
    from .assembly_protocol import VERSION
    artifacts = Path(artifacts)
    output = artifacts / "03-lego"
    presentation = artifacts / "05-presentation"
    output.mkdir(parents=True, exist_ok=True)
    presentation.mkdir(parents=True, exist_ok=True)
    graph = tools.assembly.graph()
    modules, joints = preview_hierarchy(graph)
    nodes = [n for n in graph["instances"] if n["kind"] == "part"]
    rows = [dict(part=n["part"].removesuffix(".dat"), color=n["color"], position=[r[3] for r in n["world"][:3]],
        rotation=[v for r in n["world"][:3] for v in r[:3]], module=n["parent"], step=n["step"]) for n in nodes]
    write_json(output / "assembly.json", dict(schema="assembly-desktop.v1", parts=rows,
        graph="agent/assembly-graph.json", resolver=VERSION, delivery_status="draft"))
    geometry = tools.catalog.library.geometry(rows)
    write_json(output / "agent/geometry.json", geometry)
    write_json(output / "agent/geometry.dependencies.json", tools.catalog.library.dependency_record())
    parts = [dict(id=n["id"], part_num=n["part"], color_id=n["color"], module=n["parent"], feature="",
        transform=dict(position=r["position"], rotation=r["rotation"])) for n,r in zip(nodes,rows)]
    write_json(presentation / "scene.json", dict(version="car2lego3d-scene-v1", parts=parts,
        colors=palette(tools.catalog_root),
        modules=modules,
        steps=[dict(index=i+1, module=m, parts=[n["id"] for n in nodes if n["parent"] == m and n["step"] == s])
            for i,(m,s) in enumerate(sorted({(n["parent"],n["step"]) for n in nodes}))],
        joints=joints, assembly_graph="03-lego/agent/assembly-graph.json"))
    with (output / "inventory.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["part", "color", "quantity"])
        writer.writeheader()
        writer.writerows(tools.assembly.bom())
    for name,target in (("model.io", artifacts / "04-studio/model.io"),
                        ("model.mpd",presentation / "model.mpd"), ("model.ldr",output / "model.ldr")):
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(tools.directory / name, target)
