"""Independent host review of actual resolved assemblies, never planner self-certification."""
import json

from .assembly_requirements import identity
from .common import digest, write_json


PROMPT = """Review the ACTUAL native LEGO assembly, not the reference vehicle. You are independent
of the assembly planner. Return {modules:{module_id:{status:'pass'|'fail'|'unverified',
roles:{role:{instances:[actual_part_id],evidence:string}},issues:[string]}},
repairs:{task_id:{status:'pass'|'fail'|'unverified',source_sha256:string,evidence:[string]}}}.
Cover EVERY required module and every required role. A part name or role label is not proof.
Use the supplied native geometry, graph transforms, native connectors, digital checks and actual renders
to verify each role's function and placement against the source references. Reject proxy bricks,
missing details, separated panels/glass, bare hubs without tyres and opaque glass/lenses.
When a needed role is occluded and geometry does not establish it, mark unverified. Do not infer a pass.
Require tyre/rim/hub fit and correct ownership for any included moving door glass.
Use contract.detail_standard for the required detail level. A baseline open window or LEGO lamp
representation is valid; do not demand added glass, reflectors, actuators or hidden real-car hardware
absent from that standard. Inspect every required role and all actual connections with the same rigor.
Compare distinctive vehicle features and cabin/front/rear transitions. Local image defects are mandatory
assembly corrections: check their specific required outcome, never repeat the defective reference.
This review establishes module semantics only; appearance, Studio and physical acceptance are separate.
"""


def validate_review(response, contract, bindings):
    if not isinstance(response, dict) or set(response.get("modules", {})) != set(contract["modules"]):
        raise ValueError("Independent review must cover every required module")
    for name, requirement in contract["modules"].items():
        row = response["modules"][name]
        if (row.get("status") not in {"pass", "fail", "unverified"}
                or not isinstance(row.get("issues"), list)
                or set(row.get("roles", {})) != set(requirement["roles"])):
            raise ValueError("Incomplete independent module review: " + name)
        for role, finding in row["roles"].items():
            if (not isinstance(finding.get("evidence"), str) or not finding["evidence"].strip()
                    or sorted(finding.get("instances", [])) != sorted(bindings[name]["roles"][role])):
                raise ValueError("Independent role evidence must identify the actual bound parts")
        if row["status"] == "pass" and row["issues"]:
            raise ValueError("A passing module cannot have unresolved observed issues")
    tasks = {r["id"]: r for r in contract.get("reference_repairs", [])}
    if set(response.get("repairs", {})) != set(tasks):
        raise ValueError("Independent review omitted transferred image repairs")
    for key, row in response.get("repairs", {}).items():
        if (row.get("status") not in {"pass", "fail", "unverified"}
                or row.get("source_sha256") != tasks[key]["source_sha256"]
                or not isinstance(row.get("evidence"), list) or not row["evidence"]
                or any(not isinstance(item, str) or not item.strip() for item in row["evidence"])):
            raise ValueError("Repair verdict lacks bound source and actual assembly evidence")
    return response


def review(tools):
    from .assembly_presentation import render
    from .dialogue import ask_json, image_content, settings
    report = tools.tool_module_progress()
    if not report.get("structural_ready"):
        raise ValueError("Resolve module structure, connections and motion before independent review")
    rendered = tools.tool_render()
    if rendered.get("status") != "pass":
        raise ValueError("Independent review requires completely resolved native geometry and colours")
    graph = tools.assembly.graph()
    parts = {n["part"] for n in graph["instances"] if n["kind"] == "part"}
    native = {part: tools.tool_query_part(part) for part in sorted(parts)}
    config = settings()
    key = identity(dict(graph=graph, bindings=tools.module_bindings, contract=tools.vehicle_contract,
        native=native, glb=digest(tools.directory / "assembly.glb"),
        prompt=PROMPT, reviewer=config["model"],
        references={view: sha for view, (_, sha) in tools.reference_images.items()}))
    directory = tools.directory / "module-reviews" / key
    presentation = render(tools.directory / "assembly.glb", directory / "presentation")
    images = {"ACTUAL_" + item["view"]: directory / "presentation" / item["file"] for item in presentation["images"]}
    for view in ("overview", "front", "back", "left", "right", "door_left_open", "front_lights", "rear_lights", "cockpit"):
        if view in tools.reference_images:
            path, expected = tools.reference_images[view]
            if digest(path) != expected:
                raise ValueError("Reference changed before independent review")
            images["REFERENCE_" + view] = path
    payload = dict(contract=tools.vehicle_contract, bindings=tools.module_bindings,
        graph=graph, native_parts=native, checks=json.loads((tools.directory / "checks.json").read_text(encoding="utf-8")),
        motion_summary={key: report[key] for key in ("revision", "structural_ready", "modules")})
    write_json(directory / "review-input.json", payload)
    messages = [dict(role="system", content=PROMPT), dict(role="user", content=[
        dict(type="text", text=json.dumps(payload)), *image_content(images)])]
    response = None
    for attempt in range(1, 4):
        response = ask_json(messages, directory, "review-" + str(attempt), max_tokens=16000,
            timeout=600, total_timeout=2400, reuse_completed=True)
        try:
            validate_review(response, tools.vehicle_contract, tools.module_bindings)
            break
        except (ValueError, TypeError, KeyError) as error:
            write_json(directory / f"schema-failure-{attempt}.json", dict(error=str(error), response=response))
            if attempt == 3:
                raise ValueError("Independent module review failed schema validation") from error
            messages.extend([dict(role="assistant", content=json.dumps(response)),
                dict(role="user", content="Preserve observed defects and uncertainty. Correct schema: " + str(error))])
    evidence = [directory / "review-input.json", directory / "presentation/presentation.json", *images.values()]
    # Copy external reference evidence into this immutable review folder.
    import shutil
    records = []
    for position, path in enumerate(evidence):
        if not path.resolve().is_relative_to(tools.directory):
            target = directory / "references" / (str(position) + path.suffix)
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
            path = target
        records.append(dict(file=str(path.relative_to(tools.directory)), sha256=digest(path)))
    result = dict(schema="vehicle-module-review.v1", graph_sha256=identity(graph),
        bindings_sha256=identity(tools.module_bindings), contract_sha256=identity(tools.vehicle_contract),
        reviewer=config["model"], modules=response["modules"], repairs=response.get("repairs", {}), evidence=records,
        native_evidence_files=tools.verification_inputs()["evidence_files"],
        appearance="unverified", physical="unverified")
    write_json(tools.directory / "module-role-review.json", result)
    return result
