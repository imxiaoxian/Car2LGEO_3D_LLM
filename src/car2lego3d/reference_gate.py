"""Separate image correctness from permission to model with explicit repair debt."""
from hashlib import sha256
import json
import re


VERSION = "reference-gate.v1"
LOCAL_KINDS = {"rim_geometry", "minor_trim"}
BLOCKING_KINDS = {"vehicle_identity", "body_variant", "camera", "handedness", "door_count",
                  "moving_ownership", "panel_boundary", "light_identity", "other"}
KINDS = LOCAL_KINDS | BLOCKING_KINDS


def phase_modules(task):
    if task.get("kind") == "rim_geometry":
        return ["wheels"]
    names = set(re.findall(r"[a-z0-9]+", " ".join(task.get("modules", [])).lower()))
    domains = [name for name, tokens in {
        "doors": ("door", "hinge"), "glazing": ("window", "glass"),
        "lights": ("lamp", "headlamp", "taillamp", "headlight", "taillight", "light", "lens"),
        "interior": ("seat", "dash", "dashboard", "cockpit", "cabin"),
        "wheels": ("wheel", "rim", "tyre", "tire"), "front": ("nose", "front"),
        "rear": ("rear", "tail", "engine"),
    }.items() if any(token in names or token + "s" in names for token in tokens)]
    return sorted({"body", *domains})


DEFECT_PROMPT = '''Also return defects:[{issue_index:int, kind:string, modules:[string], expected:string,
verification:string}] for EVERY issues entry (zero-based issue_index). Kinds: vehicle_identity,
body_variant, camera, handedness, door_count, moving_ownership, panel_boundary, light_identity,
rim_geometry, minor_trim, other. Use rim_geometry ONLY for localized spoke/hole/rim errors, and
minor_trim ONLY for small non-structural trim. Wrong door/window topology, hinges, handedness,
large panels or camera projection cannot be minor_trim. Expected describes the required correction;
verification describes visible evidence needed to confirm it. Uncertain hidden structures are not
visible defects; keep them in observed_state. A pass has issues:[] and defects:[].'''


def validate_defects(row):
    defects = row.get("defects")
    if defects is None:
        return  # Historical records remain readable, but cannot grant a repair waiver.
    if not isinstance(defects, list):
        raise ValueError("Reference defects must be a list")
    indices = []
    for defect in defects:
        if (not isinstance(defect, dict) or type(defect.get("issue_index")) is not int
                or defect.get("kind") not in KINDS
                or not isinstance(defect.get("modules"), list) or not defect["modules"]
                or any(not isinstance(m, str) or not m.strip() for m in defect["modules"])
                or any(not isinstance(defect.get(k), str) or not defect[k].strip()
                       for k in ("expected", "verification"))):
            raise ValueError("Incomplete structured reference defect")
        indices.append(defect["issue_index"])
    if sorted(indices) != list(range(len(row["issues"]))):
        raise ValueError("Structured defects must cover every observed issue exactly once")


def assess_reference_gate(shots):
    tasks, blocked, inferred = {}, ([] if shots else ["missing_reference_set"]), []
    for view, row in shots.items():
        if row.get("status") == "pass" and not row.get("issues") and row.get("sha256"):
            continue
        if row.get("status") != "needs_revision":
            blocked.append(view)
            continue
        defects = row.get("defects", [])
        try:
            validate_defects(row)
        except (ValueError, KeyError, TypeError):
            blocked.append(view)
            continue
        if (not row.get("sha256") or not defects
                or any(d["kind"] not in LOCAL_KINDS for d in defects)):
            blocked.append(view)
            continue
        for defect in defects:
            data = dict(view=view, source_sha256=row["sha256"], **defect)
            identity = sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
            tasks[identity] = dict(id=identity, **data, phase_modules=phase_modules(defect), status="open", evidence=[])
    return dict(schema=VERSION, status="blocked" if blocked else "ready_with_repairs" if tasks else "ready",
        allowed=not blocked, blocked_views=sorted(blocked), repair_tasks=list(tasks.values()),
        inferred_structures=inferred, image_correct=not blocked and not tasks,
        appearance_accepted=False)
