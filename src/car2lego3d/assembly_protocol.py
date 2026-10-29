"""Planner-neutral, strict JSON tool contracts."""
import json
from hashlib import sha256
from jsonschema import Draft202012Validator

VERSION = "assembly-tools.v11"

def obj(properties, required=()):
    return dict(type="object", properties=properties, required=list(required), additionalProperties=False)

S = {"type": "string", "minLength": 1}
I = {"type": "integer", "minimum": 0}
N = {"type": "number"}
B = {"type": "boolean"}
PURPOSE = {"enum": ["door", "hood", "rear_cover", "steering", "suspension", "rolling", "lamp"]}
V = dict(type="array", items=N, minItems=3, maxItems=3)
M = dict(type="array", items=dict(type="array", items=N, minItems=4, maxItems=4), minItems=4, maxItems=4)

def operation(name, properties, required):
    return obj({"op": {"const": name}, **properties}, ["op", *required])

OPERATIONS = [
    operation("module", dict(parent=S, name=dict(type="string", pattern=r"\.ldr$"), color=I, matrix=M, alias=S), ["name", "matrix"]),
    operation("add", dict(parent=S, part=S, color=I, matrix=M, alias=S), ["part", "matrix"]),
    operation("clone", dict(source=S, parent=S, color=I, matrix=M, alias=S), ["source", "matrix"]),
    operation("remove", dict(instance=S), ["instance"]),
    operation("replace", dict(instance=S, part=S), ["instance", "part"]),
    operation("color", dict(instance=S, color=I), ["instance", "color"]),
    operation("transform", dict(instance=S, matrix=M), ["instance", "matrix"]),
    operation("joint", dict(instance=S, axis=V, origin=V, limits=dict(type="array", items=N, minItems=2, maxItems=2), type={"enum": ["revolute", "prismatic"]}, purpose=PURPOSE), ["instance", "axis", "limits"]),
]
ARTICULATE = operation("articulate", dict(instance=S, source=S, source_port=S, target=S, target_port=S,
    type={"enum": ["revolute", "prismatic"]},
    limits=dict(type="array", items=N, minItems=2, maxItems=2), purpose=PURPOSE),
    ["instance", "source", "source_port", "target", "target_port", "limits", "purpose"])
BUILD_OPERATIONS = [*OPERATIONS,
    operation("attach", dict(part=S, source_port=S, target=S, target_port=S, parent=S,
        color=I, matrix=M, alias=S), ["part", "source_port", "target", "target_port"]),
    operation("snap", dict(source=S, source_port=S, target=S, target_port=S, moving=S),
        ["source", "source_port", "target", "target_port"]),
    ARTICULATE,
]
SCHEMAS = {
    "remember": obj(dict(text=dict(type="string", maxLength=128)), ["text"]),
    "context": obj(dict(resource=S, pointer={"type": "string"}, offset=I,
                        limit=dict(type="integer", minimum=1, maximum=400)), ["resource"]),
    "inspect": obj(dict(offset=I, limit=dict(type="integer", minimum=1, maximum=200))),
    "query_part": obj(dict(part=S), ["part"]),
    "query_module": obj(dict(module_id=S), ["module_id"]),
    "search_parts": obj(dict(query=S, limit=dict(type="integer", minimum=1, maximum=50)), ["query"]),
    "view_reference": obj(dict(view=S), ["view"]),
    "retrieve": obj(dict(query={"type": "string"}, types=dict(type="array", items=S, maxItems=32), min_parts=dict(type="integer", minimum=1, maximum=20000), max_parts=dict(type="integer", minimum=1, maximum=20000), limit=dict(type="integer", minimum=1, maximum=100), verified_only=B)),
    "edit": obj(dict(revision=I, operations=dict(type="array", items={"oneOf": OPERATIONS}, minItems=1, maxItems=256), strict=B), ["revision", "operations"]),
    "graft": obj(dict(revision=I, module_id=S, matrix=M, parent=S, color=I), ["revision", "module_id", "matrix"]),
    "snap": obj(dict(revision=I, source=S, source_port=S, target=S, target_port=S, moving=S, strict=B), ["revision", "source", "source_port", "target", "target_port"]),
    "search_mounts": obj(dict(revision=I, moving=S,
        targets=dict(type="array", items=S, minItems=1, maxItems=64, uniqueItems=True),
        limit=dict(type="integer", minimum=1, maximum=20),
        max_evaluations=dict(type="integer", minimum=1, maximum=256),
        max_port_pairs=dict(type="integer", minimum=1, maximum=100000),
        max_axial_samples=dict(type="integer", minimum=1, maximum=33),
        **{name: dict(type="array", items=obj(dict(instance=S, port=S), ["instance", "port"]),
                     minItems=1, maxItems=128, uniqueItems=True) for name in ("source_ports", "target_ports")}),
        ["revision", "moving", "targets"]),
    "axial_clearance": obj(dict(revision=I, moving=S, source=S, source_port=S, target=S, target_port=S,
        max_travel=dict(type="number", exclusiveMinimum=0, maximum=2000),
        max_pairs=dict(type="integer", minimum=1, maximum=100000)),
        ["revision", "moving", "source", "source_port", "target", "target_port"]),
    "axial_motion": obj(dict(revision=I, joint=S, moving=S, source=S, source_port=S, target=S, target_port=S,
        samples=dict(type="integer", minimum=9, maximum=181),
        max_travel=dict(type="number", exclusiveMinimum=0, maximum=2000),
        max_pairs=dict(type="integer", minimum=1, maximum=100000)),
        ["revision", "joint", "moving", "source", "source_port", "target", "target_port"]),
    "attach": obj(dict(revision=I, part=S, source_port=S, target=S, target_port=S,
        parent=S, color=I, matrix=M, strict=B), ["revision", "part", "source_port", "target", "target_port"]),
    "build": obj(dict(revision=I, operations=dict(type="array", items={"oneOf": BUILD_OPERATIONS},
        minItems=1, maxItems=64), strict=B), ["revision", "operations"]),
    "articulate": obj({"revision": I, **{k: v for k, v in ARTICULATE["properties"].items()
        if k != "op"}}, ["revision", *ARTICULATE["required"][1:]]),
    "bind_module": obj(dict(revision=I, module=S, roots=dict(type="array", items=S, minItems=1, uniqueItems=True),
        roles=dict(type="object", minProperties=1, additionalProperties=dict(type="array", items=S, minItems=1, uniqueItems=True))),
        ["revision", "module", "roots", "roles"]),
    "module_progress": obj({}),
    "candidate": obj(dict(revision=I, module=S, label=dict(type="string", minLength=1, maxLength=160)),
        ["revision", "module", "label"]),
    "restore_candidate": obj(dict(revision=I, module=S, candidate=S), ["revision", "module", "candidate"]),
    "check": obj({}), "motion": obj(dict(samples=dict(type="integer", minimum=9, maximum=181))),
    "render": obj({}), "preview": obj({}), "export": obj({}),
}
DECISION = obj(dict(tool={"enum": [*SCHEMAS, "finish"]}, arguments_json={"type": "string"}, reason={"type": "string"}), ["tool", "arguments_json", "reason"])

def validate_call(tool, arguments):
    if tool not in SCHEMAS:
        raise ValueError("Unknown assembly tool")
    Draft202012Validator(SCHEMAS[tool]).validate(arguments)

def protocol():
    return dict(version=VERSION, coordinates="LDraw/LDU; metres_per_ldu=0.0004", tools=SCHEMAS, decision=DECISION)

def protocol_hash():
    return sha256(json.dumps(protocol(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
