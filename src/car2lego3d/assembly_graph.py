"""Editable recursive assemblies in LDraw units, with optimistic transactions."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZipFile

import numpy as np

from .ldraw_document import color_token, parse_color, safe_name

SCHEMA = "assembly-graph.v1"
PASSWORD = b"soho0909"
LDRAW_TO_BLENDER = np.diag([.0004, -.0004, -.0004, 1.0])


def rigid(matrix, tolerance=1e-5):
    m = np.asarray(matrix, dtype=float)
    return (m.shape == (4, 4) and np.isfinite(m).all()
            and np.allclose(m[3], [0, 0, 0, 1], atol=1e-8)
            and np.allclose(m[:3, :3].T @ m[:3, :3], np.eye(3), atol=tolerance)
            and np.isclose(abs(np.linalg.det(m[:3, :3])), 1, atol=tolerance))


def reference(line):
    tokens = line.split()
    if not tokens or tokens[0] != "1":
        return None
    if len(tokens) < 15:
        raise ValueError("Incomplete LDraw reference")
    values = np.array([float(x) for x in tokens[2:14]])
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite transform")
    m = np.eye(4)
    m[:3, 3], m[:3, :3] = values[:3], values[3:].reshape(3, 3)
    return dict(color=parse_color(tokens[1]), matrix=m, name=safe_name(" ".join(tokens[14:])))


def reference_line(ref):
    m = np.asarray(ref["matrix"])
    values = np.r_[m[:3, 3], m[:3, :3].ravel()]
    return f"1 {color_token(ref['color'])} " + " ".join(f"{v:.12g}" for v in values) + " " + safe_name(ref["name"])


class Assembly:
    def __init__(self, text, source_hash=None):
        self.original = text
        self.source_hash = source_hash or sha256(text.encode()).hexdigest()
        self.revision = 0
        self.sections, self.root, self.joints = {}, None, {}
        current, prefix, pending_id = None, [], None
        for i, line in enumerate(text.splitlines()):
            tokens = line.split()
            if tokens[:2] == ["0", "FILE"]:
                current = safe_name(" ".join(tokens[2:]))
                if current in self.sections:
                    raise ValueError("Duplicate MPD section")
                self.root = self.root or current
                self.sections[current] = []
            elif tokens[:2] == ["0", "NOFILE"]:
                current = None
            elif tokens[:2] == ["0", "!C2L_ID"]:
                pending_id = tokens[2] if len(tokens) == 3 else None
            else:
                target = self.sections[current] if current else prefix
                ref = reference(line)
                uid = pending_id or sha256(f"{current}:{i}:{line}".encode()).hexdigest()[:16]
                target.append(dict(line=line, uid=uid if ref else None))
                pending_id = None
        if self.root is None:
            self.root = "main.ldr"
            self.sections[self.root], prefix = prefix, []
        self.prefix = prefix
        self.graph()

    @classmethod
    def read(cls, path):
        path = Path(path)
        if path.suffix.lower() == ".io":
            return IOArchive(path).assembly
        else:
            text = path.read_text(encoding="utf-8-sig")
        return cls(text, sha256(path.read_bytes()).hexdigest())

    def is_module(self, name):
        if name not in self.sections or name.endswith(".dat"):
            return False
        return not any(r["line"].split()[:1] in (["2"], ["3"], ["4"], ["5"]) for r in self.sections[name])

    def graph(self):
        nodes, warnings = [], []
        def visit(section, parent, world, inherited, stack):
            if section in stack:
                raise ValueError("Cyclic MPD reference")
            step, ids = 0, set()
            for row in self.sections[section]:
                if row["line"].strip() == "0 STEP":
                    step += 1
                ref = reference(row["line"])
                if ref is None:
                    continue
                if row["uid"] in ids or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", row["uid"]):
                    raise ValueError("Duplicate or invalid occurrence identity")
                ids.add(row["uid"])
                identity = parent + "/" + row["uid"]
                matrix = world @ ref["matrix"]
                color = inherited if ref["color"] == 16 else ref["color"]
                module = self.is_module(ref["name"])
                if ref["name"] not in self.sections and not ref["name"].endswith(".dat"):
                    raise ValueError(f"Missing submodel: {ref['name']}")
                node = dict(id=identity, parent=parent, section=section, source_ref=row["uid"],
                            part=ref["name"], kind="module" if module else "part",
                            color=color, local_color=ref["color"], step=step,
                            local=ref["matrix"].tolist(), world=matrix.tolist(),
                            embedded=ref["name"] in self.sections)
                nodes.append(node)
                if len(nodes) > 250000:
                    raise ValueError("Assembly expansion limit exceeded")
                if not rigid(ref["matrix"]):
                    warnings.append(dict(instance=identity, issue="imported_nonrigid_transform", status="unverified"))
                if module:
                    visit(ref["name"], identity, matrix, color, (*stack, section))
        visit(self.root, "root", np.eye(4), 16, ())
        return dict(schema=SCHEMA, revision=self.revision, source_sha256=self.source_hash,
                    coordinates=dict(frame="LDraw", unit="LDU", metres_per_ldu=.0004,
                                     blender_matrix=LDRAW_TO_BLENDER.tolist()),
                    root=self.root, instances=nodes, joints=deepcopy(self.joints),
                    connections=[], connection_status="unverified", warnings=warnings)

    def text(self):
        if self.revision == 0:
            return self.original
        lines = [r["line"] for r in self.prefix]
        for name, rows in self.sections.items():
            lines.append("0 FILE " + name)
            for row in rows:
                if row["uid"]:
                    lines.append("0 !C2L_ID " + row["uid"])
                lines.append(row["line"])
            lines.append("0 NOFILE")
        return "\n".join(lines) + "\n"

    def _new_id(self, purpose):
        # Stable occurrence IDs let the exact same tool trace replay independently.
        content = json.dumps([self.source_hash, self.revision, purpose, self.sections],
                             sort_keys=True, separators=(",", ":"))
        return sha256(content.encode()).hexdigest()[:16]

    def _locate(self, identity):
        tokens = identity.split("/")
        if tokens[0] != "root" or len(tokens) < 2:
            raise ValueError("Invalid instance path")
        section = self.root
        for depth, uid in enumerate(tokens[1:]):
            row = next((r for r in self.sections[section] if r["uid"] == uid), None)
            if row is None:
                raise ValueError("Unknown instance")
            if depth == len(tokens) - 2:
                return section, row
            ref = reference(row["line"])
            if not self.is_module(ref["name"]):
                raise ValueError("Instance path enters a leaf")
            # Clone the entire path so a shared definition is never edited in place.
            clone = "c2l-" + self._new_id(identity) + ".ldr"
            self.sections[clone] = deepcopy(self.sections[ref["name"]])
            ref["name"] = clone
            row["line"] = reference_line(ref)
            section = clone

    def transact(self, revision, operations, validator=None):
        if revision != self.revision:
            raise ValueError("Stale assembly revision")
        before = deepcopy((self.sections, self.joints, self.revision))
        try:
            aliases, clones = {}, []
            for original in operations:
                op = deepcopy(original)
                alias = op.pop("alias", None)
                if alias is not None and (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", alias) or alias in aliases):
                    raise ValueError("Invalid or duplicate batch alias")
                for key in ("parent", "instance", "source"):
                    if op.get(key, "").startswith("@"):
                        name = op[key][1:]
                        if name not in aliases:
                            raise ValueError("Unresolved batch alias: " + name)
                        op[key] = aliases[name]
                action = op["op"]
                if alias is not None and action not in {"module", "add", "clone"}:
                    raise ValueError("Batch alias requires a new instance")
                cloned = None
                if action == "clone":
                    nodes = {node["id"]: node for node in self.graph()["instances"]}
                    source = op["source"]
                    if source not in nodes:
                        raise ValueError("Unknown clone source")
                    parent = op.get("parent", "root")
                    if parent == source or parent.startswith(source + "/"):
                        raise ValueError("Clone parent cannot be inside the source subtree")
                    cloned = dict(source=source, members=[key for key in nodes
                        if key == source or key.startswith(source + "/")],
                        joints={key: deepcopy(value) for key, value in self.joints.items()
                            if key.startswith(source + "/")}, root_joint_omitted=source in self.joints)
                    op = {**op, "part": nodes[source]["part"], "color": op.get("color", nodes[source]["color"])}
                    action = "add"
                if action == "module":
                    name = safe_name(op["name"])
                    if not name.endswith(".ldr") or name in self.sections:
                        raise ValueError("New module requires a unique .ldr name")
                    self.sections[name] = []
                    op = {**op, "part": name}
                    action = "add"
                if action == "add":
                    parent = op.get("parent", "root")
                    section = self.root
                    if parent != "root":
                        _, target = self._locate(parent)
                        target_ref = reference(target["line"])
                        if not self.is_module(target_ref["name"]):
                            raise ValueError("Parent must be a module")
                        clone = "c2l-" + self._new_id(parent) + ".ldr"
                        self.sections[clone] = deepcopy(self.sections[target_ref["name"]])
                        target_ref["name"] = clone
                        target["line"] = reference_line(target_ref)
                        section = clone
                    ref = dict(name=safe_name(op["part"]), color=int(op.get("color", 16)), matrix=op["matrix"])
                    if not rigid(ref["matrix"]):
                        raise ValueError("Standard parts cannot be scaled or sheared")
                    uid = self._new_id(section)
                    self.sections[section].append(dict(line=reference_line(ref), uid=uid))
                    if alias is not None:
                        aliases[alias] = parent + "/" + uid
                    if cloned is not None:
                        target = parent + "/" + uid
                        mapping = {key: target + key[len(cloned["source"]):] for key in cloned["members"]}
                        # Descendant joints use unchanged parent-local frames. The root
                        # joint connects outside this subtree and must be articulated anew.
                        for key, joint in cloned["joints"].items():
                            joint["instance"] = mapping[key]
                            self.joints[mapping[key]] = joint
                        clones.append(dict(source=cloned["source"], instance=target, instances=mapping,
                            joints_copied=len(cloned["joints"]), root_joint_omitted=cloned["root_joint_omitted"]))
                elif action == "joint":
                    target = op["instance"]
                    if target not in {n["id"] for n in self.graph()["instances"]}:
                        raise ValueError("Unknown joint instance")
                    axis = np.asarray(op["axis"], dtype=float)
                    limits = np.asarray(op["limits"], dtype=float)
                    origin = np.asarray(op.get("origin", [0, 0, 0]), dtype=float)
                    if op.get("type", "revolute") not in {"revolute", "prismatic"} or origin.shape != (3,) or not np.isfinite(origin).all():
                        raise ValueError("Invalid joint type or origin")
                    if axis.shape != (3,) or not np.isfinite(axis).all() or not np.isclose(np.linalg.norm(axis), 1):
                        raise ValueError("Joint axis must be a unit vector")
                    if limits.shape != (2,) or not np.isfinite(limits).all() or limits[0] > limits[1]:
                        raise ValueError("Invalid joint limits")
                    self.joints[target] = deepcopy(op)
                else:
                    section, row = self._locate(op["instance"])
                    ref = reference(row["line"])
                    if action == "remove":
                        self.sections[section].remove(row)
                        prefix = op["instance"]
                        self.joints = {k: v for k, v in self.joints.items() if k != prefix and not k.startswith(prefix + "/")}
                    elif action in {"transform", "replace", "color"}:
                        if "matrix" in op:
                            if not rigid(op["matrix"]):
                                raise ValueError("Standard parts cannot be scaled or sheared")
                            ref["matrix"] = op["matrix"]
                        if "part" in op:
                            ref["name"] = safe_name(op["part"])
                            prefix = op["instance"]
                            self.joints = {k: v for k, v in self.joints.items() if k != prefix and not k.startswith(prefix + "/")}
                        if "color" in op:
                            ref["color"] = int(op["color"])
                        row["line"] = reference_line(ref)
                    else:
                        raise ValueError(f"Unsupported assembly operation: {action}")
            self.revision += 1
            graph = self.graph()
            if validator is not None:
                result = validator(graph)
                if result.get("status") == "fail":
                    raise ValueError("Transaction validation failed: " + json.dumps(result))
            return {**graph, **({"aliases": aliases} if aliases else {}), **({"clones": clones} if clones else {})}
        except Exception:
            self.sections, self.joints, self.revision = before
            raise

    def bom(self):
        counts = Counter((n["part"], n["color"]) for n in self.graph()["instances"] if n["kind"] == "part")
        return [dict(part=p, color=c, quantity=n) for (p, c), n in sorted(counts.items())]


class IOArchive:
    """Keep every unknown ZIP member; rebuild only supported model documents."""
    def __init__(self, path):
        self.path = Path(path)
        with ZipFile(path) as z:
            self.comment = z.comment
            self.members = [(deepcopy(i), z.read(i, pwd=PASSWORD)) for i in z.infolist()]
        names = [i.filename for i, _ in self.members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members are ambiguous")
        self.assembly = Assembly(dict((i.filename, b) for i, b in self.members)["model.ldr"].decode("utf-8-sig"),
                                 sha256(self.path.read_bytes()).hexdigest())
        payloads = {i.filename: b for i, b in self.members}
        if "car2lego/assembly-graph.json" in payloads:
            saved = json.loads(payloads["car2lego/assembly-graph.json"])
            if saved.get("schema") != SCHEMA or saved.get("instances") != self.assembly.graph()["instances"]:
                raise ValueError("Persisted graph differs from archive model")
            revision = saved.get("revision")
            if type(revision) is not int or revision < 0:
                raise ValueError("Invalid persisted revision")
            self.assembly.revision = revision
            self.assembly.joints = saved.get("joints", {})
        self.import_revision = self.assembly.revision

    def export(self, destination, dependencies=None):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Export to a new file to preserve the source archive")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.assembly.revision == self.import_revision and not dependencies:
            destination.write_bytes(self.path.read_bytes())
            return dict(status="pass", unchanged=True, archive_roundtrip_verified=True)
        payload = self.assembly.text().encode("utf-8")
        temporary = destination.with_suffix(".io.tmp")
        stale = []
        try:
            with ZipFile(temporary, "w") as out:
                out.comment = self.comment
                for info, data in self.members:
                    if dependencies and info.filename.startswith("CustomParts/") and info.filename[len("CustomParts/"):] in dependencies:
                        continue
                    if info.filename in {"car2lego/assembly-graph.json", "car2lego/edit-report.json"}:
                        continue
                    if info.filename in {"model.ldr", "model2.ldr", "modelv2.ldr"}:
                        data = payload
                    elif info.filename == ".info":
                        try:
                            metadata = json.loads(data)
                            metadata["total_parts"] = sum(x["quantity"] for x in self.assembly.bom())
                            data = json.dumps(metadata, ensure_ascii=False).encode()
                        except (ValueError, TypeError):
                            stale.append(info.filename)
                    elif info.filename.endswith(".lxfml") or info.filename.lower().endswith((".png", ".jpg")):
                        stale.append(info.filename)
                    out.writestr(info, data)
                for name, data in (dependencies or {}).items():
                    out.writestr("CustomParts/" + safe_name(name), data)
                out.writestr("car2lego/assembly-graph.json", json.dumps(self.assembly.graph()).encode())
                out.writestr("car2lego/edit-report.json", json.dumps(dict(stale_derived_members=stale)).encode())
            check = IOArchive(temporary)
            if check.assembly.bom() != self.assembly.bom():
                raise ValueError("Archive roundtrip changed inventory")
            if check.assembly.graph()["instances"] != self.assembly.graph()["instances"]:
                raise ValueError("Archive roundtrip changed assembly graph")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return dict(status="unverified" if stale else "pass", archive_roundtrip_verified=True,
                    stale_derived_members=stale, studio_open_verified=False, physical_verified=False)
