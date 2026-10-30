"""Exercise a native four-wheel chassis without claiming whole-vehicle acceptance."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

import numpy as np
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash, wire_json
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_layout import wheel_layout
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.common import digest, write_json
from car2lego3d.assembly_tools import AssemblyTools
from scripts.probe_connector_module import recovered_errors


PARTS = {"3035.dat": 1, "3700.dat": 4, "3705.dat": 4, "56145.dat": 4, "61481.dat": 4}


def demonstration_operations():
    operations = [dict(op="module", name="chassis.ldr", alias="chassis", matrix=np.eye(4).tolist()),
        dict(op="add", part="3035.dat", color=71, parent="@chassis", alias="deck", matrix=np.eye(4).tolist())]
    axle = np.eye(4)
    axle[:3, :3] = Rotation.from_euler("y", -90, degrees=True).as_matrix()
    axle[2, 3] = 20
    for name, port, right in (("fl", "0:7:1", False), ("fr", "0:1:3", True),
                              ("rl", "0:7:13", False), ("rr", "0:1:15", True)):
        orientation = np.eye(4)
        orientation[:3, :3] = Rotation.from_euler("y", 180 if right else 0, degrees=True).as_matrix()
        wheel = orientation.copy()
        # Seed a legal axial seating; snap still validates native overlap and stops.
        wheel[2, 3] = 80 if right else -80
        operations.extend([
            dict(op="attach", part="3700.dat", color=0, parent="@chassis", alias=name + "bearing",
                source_port="0:1:1", target="@deck", target_port=port, matrix=orientation.tolist()),
            dict(op="module", name=name + ".ldr", alias=name, matrix=wheel.tolist()),
            dict(op="add", part="56145.dat", color=71, parent="@" + name,
                alias=name + "rim", matrix=np.eye(4).tolist()),
            dict(op="attach", part="61481.dat", color=0, parent="@" + name, alias=name + "tyre",
                source_port="0", target="@" + name + "rim", target_port="2"),
            dict(op="attach", part="3705.dat", color=0, parent="@" + name, alias=name + "axle",
                source_port="0", target="@" + name + "rim", target_port="0", matrix=axle.tolist()),
            dict(op="snap", moving="@" + name, source="@" + name + "axle", source_port="0",
                target="@" + name + "bearing", target_port="2"),
            dict(op="articulate", instance="@" + name, source="@" + name + "axle", source_port="0",
                target="@" + name + "bearing", target_port="2", limits=[0, 360], purpose="rolling")])
    return operations


def chassis_evidence(tools, *, autonomous=False):
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    parts = {key: node for key, node in nodes.items() if node["kind"] == "part"}
    issues, wheels, movement = [], [], {}
    roles, descriptions = {}, {}
    if autonomous:
        from scripts.probe_autonomous_wheel import native_role
        for part in {node["part"] for node in parts.values()}:
            try:
                roles[part], descriptions[part] = native_role(tools.catalog, part)
            except (OSError, ValueError) as error:
                issues.append("missing_native_part_evidence:" + part)
                descriptions[part] = str(error)
        if not 17 <= len(parts) <= 64:
            issues.append("requires_seventeen_to_sixty_four_parts")
    else:
        roles = {"56145.dat": "rim", "61481.dat": "tyre", "3705.dat": "axle"}

    def report(name):
        path = tools.directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    checks, motion = report("checks.json"), report("motion.json")
    for label, result in (("check", checks), ("motion", motion)):
        if result.get("status") != "pass" or not check_current(graph, result):
            issues.append(label + "_missing_failed_or_stale")
    if not autonomous and Counter(node["part"] for node in parts.values()) != Counter(PARTS):
        issues.append("requires_one_deck_and_four_rim_tyre_axle_bearing_sets")
    if len(graph["joints"]) != 4 or any(j.get("purpose") != "rolling" for j in graph["joints"].values()):
        issues.append("requires_four_independent_rolling_joints")
    if issues:
        return dict(status="fail", issues=issues, physical="unverified")

    supports, moving_ids = set(), set()
    for name, joint in graph["joints"].items():
        members = {key: node for key, node in parts.items() if key.startswith(name + "/")}
        if nodes[name]["kind"] != "module" or Counter(roles.get(n["part"]) for n in members.values()) != Counter(
                {"rim": 1, "tyre": 1, "axle": 1}):
            issues.append("rolling_subtree_requires_one_rim_tyre_and_axle_only")
            continue
        if moving_ids.intersection(members):
            issues.append("rolling_subtrees_overlap")
        moving_ids.update(members)
        by_role = {roles[node["part"]]: key for key, node in members.items()}
        axle = by_role["axle"]
        contacts = []
        for edge in checks.get("connections", []):
            a, b = edge["a"], edge["b"]
            if axle in (a, b):
                other, port = (b, edge["port_b"]) if a == axle else (a, edge["port_a"])
                if other not in members and other in parts:
                    bearing = next((p for p in tools.catalog.ports(parts[other]["part"]) if p.id == port), None)
                    if (autonomous and bearing and bearing.kind == "axle"
                            and bearing.semantic in {"pin_socket", "round_hole"}) or (
                            not autonomous and parts[other]["part"] == "3700.dat"):
                        contacts.append((other, port))
        if len(contacts) != 1:
            issues.append("wheel_requires_one_fixed_native_bearing_contact")
            continue
        support, port_id = contacts[0]
        supports.add(support)
        if any(support == root or support.startswith(root + "/") for root in graph["joints"]):
            issues.append("bearing_must_be_outside_every_moving_subtree")
        edges = {frozenset((row["a"], row["b"])) for row in checks.get("connections", [])}
        if any(frozenset((by_role[a], by_role[b])) not in edges for a, b in (("axle", "rim"), ("rim", "tyre"))):
            issues.append("wheel_requires_native_axle_rim_and_rim_tyre_contacts")
        parent = nodes[name]["parent"]
        parent_world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
        axis = parent_world[:3, :3] @ np.asarray(joint["axis"])
        pivot = (parent_world @ [*joint["origin"], 1])[:3]
        port = next(p for p in tools.catalog.ports(parts[support]["part"]) if p.id == port_id)
        bearing_world = np.asarray(parts[support]["world"])
        bearing_axis = bearing_world[:3, :3] @ port.direction
        offset = pivot - (bearing_world @ [*port.position, 1])[:3]
        if (abs(axis @ bearing_axis) < .9999 or abs(axis[2]) < .9999
                or np.linalg.norm(np.cross(offset, bearing_axis)) > .25):
            issues.append("rolling_axis_must_follow_actual_transverse_bearing")
        sweep = next((row for row in motion.get("joints", []) if row["instance"] == name), {})
        values = sorted({row["value"] for row in sweep.get("poses", [])})
        if len(values) < 9 or values[-1] - values[0] < 360 - 1e-8:
            issues.append("requires_nine_distinct_poses_across_360_degrees_per_wheel")
        for value in values:
            posed = {node["id"]: node for node in posed_graph(graph, {name: value})["instances"]}
            if any(not np.allclose(posed[key]["world"], node["world"], atol=1e-6)
                    for key, node in parts.items() if key not in members):
                issues.append("other_wheels_or_fixed_frame_moved")
            for key, node in members.items():
                triangles, _ = tools.catalog.library.triangles(node["part"])
                vertices = np.asarray(triangles).reshape(-1, 3)
                delta = np.asarray(posed[key]["world"]) - np.asarray(node["world"])
                displacement = np.linalg.norm(vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()
                movement[key] = max(movement.get(key, 0), float(displacement))
        if any(movement.get(key, 0) <= 1 for key in members):
            issues.append("actual_rim_tyre_and_axle_geometry_must_rotate")
        tyre = by_role["tyre"]
        triangles, _ = tools.catalog.library.triangles(members[tyre]["part"])
        vertices = np.asarray(triangles).reshape(-1, 3)
        world = np.asarray(members[tyre]["world"])
        surface = vertices @ world[:3, :3].T + world[:3, 3]
        wheels.append(dict(root=name, tyre=tyre, support=support, poses=len(values),
            span_degrees=values[-1] - values[0] if values else 0,
            center=((surface.min(axis=0) + surface.max(axis=0)) / 2).tolist()))

    if len(supports) != 4 or supports.intersection(moving_ids):
        issues.append("requires_four_distinct_fixed_bearings")
    layout = None
    if len(wheels) == 4:
        ordered = sorted(wheels, key=lambda row: row["center"][0])
        frame_roots = set()
        for key in set(parts) - moving_ids:
            root = nodes[key]["parent"]
            if root == "root":
                issues.append("fixed_frame_requires_actual_submodel_roots")
                continue
            while nodes[root]["parent"] != "root":
                root = nodes[root]["parent"]
            frame_roots.add(root)
        bindings = {"chassis": dict(roots=sorted(frame_roots))}
        for axle, pair in (("front", ordered[:2]), ("rear", ordered[2:])):
            for side, wheel in zip(("left", "right"), sorted(pair, key=lambda row: row["center"][2])):
                bindings[f"wheel_{axle}_{side}"] = dict(roots=[wheel["root"]], roles=dict(tyre=[wheel["tyre"]]))
        layout = wheel_layout(graph, tools.catalog, dict(modules={key: {} for key in bindings}), bindings)
        if layout["status"] != "pass" or not layout["complete"]:
            issues.append("native_wheel_layout_failed")
    else:
        issues.append("requires_four_measured_wheels")
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), wheels=wheels,
        maximum_vertex_displacement_ldu=movement, layout=layout, physical="unverified",
        **(dict(native_descriptions=descriptions) if autonomous else {}))


def run(output, native, *, live=False, model=None, timeout=1200):
    output, native = Path(output).resolve(), Path(native).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve previous chassis evidence")
    tools = AssemblyTools(output / "assembly", native)
    parts = {part: tools.tool_query_part(part) for part in PARTS}
    mission = dict(request=dict(parts=PARTS, goal=
        "Build a connected four-wheel rolling chassis from exactly the supplied native parts and counts. "
        "Keep the deck and four bearing bricks in a fixed frame submodel. Each of four independent moving modules contains "
        "one rim, tyre and axle; mount it on a distinct fixed bearing using observed native connectors. "
        "Choose placements and axial seating from actual bounds and ports. Preserve standard dimensions. "
        "X is front-to-rear, Y down, Z left-to-right. Align each axle pair, separate left and right tyres, "
        "share one ground plane and centreline, and keep the chassis above the tyre ground plane. "
        "All parts must connect with no native collider interference. Use articulate for verified rolling "
        "contacts with 360-degree ranges, then check and motion(samples=9). Preview and inspect the actual "
        "pixels, export, finish. Tyres black (0), rims light bluish gray (71). "
        "This is a fixed-part structural diagnostic, not three candidate module acceptance, a complete "
        "vehicle, steering, suspension, physical axle retention or appearance acceptance. "
        "Read guide:/build_operations signatures and native_parts:/<part.dat> as needed. "
        "@aliases exist only inside a build call; retain returned real IDs for later calls."),
        native_parts=parts, module_task=dict(id="four_wheel_rolling_chassis_diagnostic", motion_samples=9))
    probe_hash = digest(Path(__file__))
    if live:
        backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    else:
        class Demonstrator:
            name = "executed_chassis_demonstration"
            actions = [("build", dict(revision=0, operations=demonstration_operations())), ("check", {}),
                ("motion", dict(samples=9)), ("preview", {}), ("export", {}), ("finish", {})]

            def propose(self, payload, step):
                tool, arguments = self.actions[step]
                return dict(tool=tool, arguments_json=wire_json(arguments), reason="Execute chassis diagnostic")
        backend = Demonstrator()
    started = time.monotonic()
    agent = AssemblyAgent(tools, backend, mission, max_steps=64, max_seconds=timeout,
        finish_check=lambda: chassis_evidence(tools))
    state = agent.run()
    trace = [json.loads(row) for row in (tools.directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    graph = tools.assembly.graph()
    archive = tools.directory / "model.io"
    preview_path = tools.directory / "preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else None
    pixels = [dict(step=row["step"], tool=row["decision"]["tool"], image_sha256=image["sha256"])
        for row in trace for image in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and image["sha256"] == preview["image"]["sha256"]]
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {item["failed_step"] for item in recoveries}
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    evidence = chassis_evidence(tools)
    record = dict(schema="rolling-chassis-probe.v1", live_planner=live, planner=backend.name,
        input_hash=agent.input_hash, execution_hash=agent.contract["execution_hash"],
        execution_unchanged=execution_hash() == agent.contract["execution_hash"],
        protocol_hash=agent.contract["protocol_hash"], planner_context=agent.contract["planner_context"],
        probe_sha256=probe_hash, probe_unchanged=digest(Path(__file__)) == probe_hash,
        native_parts=parts, native_files=tools.catalog.library.dependency_record(),
        seconds=time.monotonic() - started, stop_reason=state["stop_reason"],
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=[row for row in errors if row["step"] not in recovered],
        parts=sum(n["kind"] == "part" for n in graph["instances"]), digital=state.get("digital_status"),
        archive_roundtrip=archive.is_file() and IOArchive(archive).assembly.bom() == tools.assembly.bom(),
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        pixel_feedback=pixels, chassis=evidence,
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One fixed-part four-wheel rolling chassis diagnostic",
        limitations="Supplied part types and counts. No three-candidate module, vehicle, steering, suspension, "
            "physical retention, load capacity or general-object acceptance.")
    passed = (evidence["status"] == "pass" and record["digital"] == "pass" and record["archive_roundtrip"]
        and state["stop_reason"] == "planner_finished" and record["execution_unchanged"] and record["probe_unchanged"]
        and {"check", "motion", "preview", "export", "finish"} <= successful
        and bool(successful.intersection({"build", "edit", "attach"})) and (not live or bool(pixels)))
    record["first_pass_module_loop_pass"] = passed and not errors
    record["module_loop_pass"] = passed and not record["unrecovered_planner_errors"]
    write_json(output / "report.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    result = run(args.output, args.native, live=args.live, model=args.model, timeout=args.timeout)
    print(json.dumps({key: result[key] for key in ("module_loop_pass", "planner_actions", "parts", "digital", "seconds")}))
    if not result["module_loop_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
