"""Versioned assembly tools shared by every planner backend."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from pathlib import Path
from threading import RLock
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from scipy.spatial.transform import Rotation
from jsonschema import Draft202012Validator, ValidationError

from .assembly_catalog import NativeCatalog, compatible, shaft_snap_origin, validate
from .assembly_corpus import Corpus
from .assembly_graph import Assembly, IOArchive, reference, reference_line, rigid
from .common import write_json
from .job_runtime import AssemblyControlError

from .assembly_protocol import SCHEMAS, VERSION as PROTOCOL
TOOLS = {
    "remember": "Persist a concise work note across decisions. args: text (<=128 characters). Use resource pointers for exact large data.",
    "context": "Read complete context incrementally. args: resource, pointer (JSON pointer), offset=0, limit=400. Pages report continuation; nothing is silently truncated.",
    "inspect": "Read paged instance graph, revision, joints and BOM. args: offset=0, limit=50.",
    "query_part": "Read part geometry/connector/collider evidence. args: part.",
    "query_module": "Inspect a retrieved module's actual native bounds and BOM without grafting it. args: module_id. Full graph is a context resource.",
    "search_parts": "Search native English descriptions or part filenames. args: query, limit=20. Whole-word tire/tyre and centre/center spellings are equivalent; frame also finds Technic open-center beams, after literal matches. Numeric terms match complete sizes (30.4 stays decimal; 4 does not match 40 or filename digits). Explicit .dat filenames are exact; bare IDs still find filename variants. Query actual dimensions/ports with query_part before fitting.",
    "view_reference": "Select a verified reference image for the vision planner's next decision. args: view.",
    "retrieve": "Find modules by model/submodel names or tags. args: query, types, min_parts, max_parts, limit, verified_only. types filters vehicle body categories, not module kinds; omit it to search all. Inspect bounds/interfaces before grafting. Test models stay excluded.",
    "edit": "Atomic operations with revision. New modules need .ldr names. Add/module/clone can declare alias; later operations in the same batch use @alias for parent/instance/source. clone copies an existing subtree at a rigid parent-local matrix, preserves descendant joints and effective colours, omits the source root joint (re-articulate at its new support), and returns an instance map. Copies edit independently. Matrices are local LDraw/LDU; joint angles degrees.",
    "graft": "Transplant a retrieved module without scaling. args: revision, module_id, parent=root, matrix (4x4), color=16.",
    "snap": "Rigidly connect source instance port to target instance port. args: revision, source, source_port, target, target_port, moving optional ancestor module. Moves the named subtree together; seats shaft intervals respecting stops, preserves shaft facing and roll; orient keyed ports first. Check collisions separately or use strict.",
    "search_mounts": "Read-only bounded placement search for an existing moving part/subtree against targets outside it. args: revision,moving,targets,limit=6,max_evaluations=64,max_port_pairs=20000,max_axial_samples=9 (1 disables axial alternatives, maximum 33); optional source_ports/target_ports arrays of {instance,port}. Returns exact native contacts and replayable build_arguments; 24 relative axis rotations and finite shaft insertion samples respecting native stops/keys. No new parts or exhaustive continuous search. Installation pass differs from whole-graph pass; motion and vehicle acceptance require separate checks.",
    "axial_clearance": "Read-only continuous native-OBB sweep of an engaged shaft's moving subtree in both axial directions until selected port intervals disengage. args: revision,moving,source,source_port,target,target_port,max_travel=400 LDU,max_pairs=100000. Reports clear/blocked/unverified paths and first blockers. Missing evidence, initial interference or exhausted budgets cannot establish a clear path. Geometric diagnosis only; friction, physical retention and load remain unverified.",
    "axial_motion": "Read-only axial_clearance across sampled positions of one declared joint. args: revision,joint,moving,source,source_port,target,target_port,samples=9,max_travel=400,max_pairs=100000 (per pose). moving is the joint subtree or an explicit descendant cut. Check whole modules AND individual shaft/bush/hub cuts; grouping does not fix internal sliding. Returns sampled geometric classification, first blockers and release paths. Other joints neutral; intermediate angles, insertion order, physical retention and load remain unverified.",
    "attach": "Add a real part and seat its observed source_port on target/target_port atomically. args: revision,part,source_port,target,target_port,parent=root,color=16,matrix optional local initial orientation,strict=true. No translation matrix needed; collisions or missing validation evidence roll back the new part.",
    "articulate": "Declare a joint at an already verified native axle/hinge contact. args: revision,instance (moving subtree),source,source_port,target,target_port,limits,purpose,type=revolute. Source must be inside instance, target outside. Host derives parent-local axis/origin from target port; rotations use degrees, rolling spans 360. Run motion afterwards; declaration is not motion acceptance.",
    "build": "Build 1..64 operations in one atomic revision: edit operations plus attach/snap/articulate with real native ports. Aliases are scoped to this build call only; use returned real instance IDs in later calls. Default strict=true requires a complete final native check; any failure rolls back the whole batch. No intermediate saves.",
    "check": "Validate geometry, every connector contact, collisions, connectivity and neutral native-tyre ground/axle layout. Missing wheels or native evidence cannot complete a vehicle. args: none.",
    "bind_module": "Bind a host-required vehicle module to actual submodel roots and distinct part IDs per role. args: revision,module,roots,roles. Labels alone do not validate roles; changes invalidate checks and reviews.",
    "module_progress": "Check required vehicle modules, actual role parts, interfaces, joints, current checks and independent role evidence. args: none. Missing evidence is unverified; draft export is always allowed.",
    "candidate": "Measure and checkpoint an actually assembled module candidate. args: revision,module,label. Compare three distinct passing structures before finishing; text descriptions do not count.",
    "restore_candidate": "Restore a measured module trial when the surrounding assembly is unchanged. args: revision,module,candidate. Revision remains monotonic; checks must be repeated.",
    "motion": "Check at least nine positions of each declared joint. args: samples=9. Digital only.",
    "render": "Export actual part-geometry GLB using the same resolver. args: none. No invented geometry.",
    "preview": "Render six whole-object views of current resolved native parts into a source-bound contact sheet. args: none. Vision planners receive the sheet in the next decision. Missing geometry/colours block preview; images are not an appearance pass.",
    "export": "Write nested MPD, flat LDR, IO, BOM, assembly steps and checks. args: none. Export does not certify appearance.",
}


def compact(graph, offset=0, limit=50):
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid graph page")
    return {**{k: graph[k] for k in ("schema", "revision", "coordinates", "root", "joints", "warnings")},
            "total": len(graph["instances"]), "offset": offset, "instances": graph["instances"][offset:offset+limit]}


def _json_pointer(path):
    return "/" + "/".join(str(item).replace("~", "~0").replace("/", "~1") for item in path)


def validation_diagnostic(tool, arguments, error):
    """Return the actionable branch error while preserving jsonschema's full evidence."""
    detail = error
    prefix = list(error.path)
    branch = None
    schema = SCHEMAS.get(tool)
    if error.validator == "oneOf" and schema and error.context:
        node = schema
        value = arguments
        try:
            for key in error.path:
                if isinstance(value, list):
                    node, value = node["items"], value[key]
                else:
                    node, value = node["properties"][key], value[key]
            if isinstance(value, dict):
                branch = value.get("op")
            selected = next((candidate for candidate in node.get("oneOf", [])
                             if candidate.get("properties", {}).get("op", {}).get("const") == branch), None)
            if selected is not None:
                candidates = list(Draft202012Validator(selected).iter_errors(value))
                if candidates:
                    priority = {"pattern": 0, "type": 1, "required": 2, "minLength": 3,
                                "additionalProperties": 4, "minimum": 5}
                    detail = min(candidates, key=lambda item: priority.get(item.validator, 9))
        except (KeyError, IndexError, TypeError):
            detail = error
    path = prefix + list(detail.path) if detail is not error else prefix
    result = dict(kind="schema_validation", tool=tool, path=_json_pointer(path),
                  validator=detail.validator, message=detail.message)
    if branch:
        result["operation"] = branch
    if detail.validator in {"pattern", "required", "enum", "const", "minLength", "minItems", "maxItems"}:
        result["expected"] = detail.validator_value
    return result


class AssemblyTools:
    def __init__(self, directory, catalog_root, corpus=None, *, exclude_models=(), exclude_families=(), exclude_hashes=()):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.corpus = corpus
        self.catalog_root = Path(catalog_root)
        self.catalog = NativeCatalog(catalog_root)
        self.exclude_models = tuple(exclude_models)
        self.exclude_families, self.exclude_hashes = tuple(exclude_families), tuple(exclude_hashes)
        self.exclusions = corpus.exclusions(exclude_models=exclude_models, exclude_families=exclude_families,
            exclude_hashes=exclude_hashes) if corpus else dict(models=sorted(exclude_models),
                families=sorted(exclude_families), hashes=sorted(exclude_hashes))
        self.lock = RLock()
        self.checkpoint = None
        self.archive = None
        self.context_resources = {}
        self.reference_images = {}
        self.vehicle_contract, self.module_bindings = None, {}
        self.source_archive = None
        self.assembly = Assembly("0 FILE main.ldr\n0 Car2LEGO independent assembly\n0 NOFILE\n")
        checkpoint = self.directory / "state.json"
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assembly = Assembly(state["text"], state["source_sha256"])
            self.assembly.revision, self.assembly.joints = state["revision"], state["joints"]
            self.module_bindings = state.get("module_bindings", {})
            if state.get("source_archive"):
                self.source_archive = self.directory / state["source_archive"]
                self.archive = IOArchive(self.source_archive)
            packages = [self.directory / p for p in state.get("packages", [])]
            self.catalog = NativeCatalog(catalog_root, packages)
        self._embedded_geometry()

    def _embedded_geometry(self):
        package = self.directory / "packages/embedded"
        found = False
        for name, rows in self.assembly.sections.items():
            if name == self.assembly.root or self.assembly.is_module(name):
                continue
            path = package / "parts" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(r["line"] for r in rows) + "\n", encoding="utf-8")
            found = True
        if found:
            self.catalog = NativeCatalog(self.catalog_root, list(dict.fromkeys([package, *self.catalog.packages])))

    def dependencies(self):
        result = {}
        for package in self.catalog.packages:
            for path in sorted(package.rglob("*")):
                if not path.is_file():
                    continue
                name, data = path.relative_to(package).as_posix(), path.read_bytes()
                if name in result and result[name] != data:
                    raise ValueError("Conflicting packaged dependency: " + name)
                result[name] = data
        return result

    def save(self):
        write_json(self.directory / "state.json", dict(text=self.assembly.text(), revision=self.assembly.revision,
            module_bindings=self.module_bindings,
            joints=self.assembly.joints, source_sha256=self.assembly.source_hash,
            packages=[str(p.relative_to(self.directory)) for p in self.catalog.packages],
            source_archive=str(self.source_archive.relative_to(self.directory)) if self.source_archive else None))

    def import_file(self, path):
        from .assembly_corpus import embedded_packages
        path = Path(path)
        self.module_bindings = {}
        if path.suffix.lower() == ".io":
            source = self.directory / "sources" / (sha256(path.read_bytes()).hexdigest() + ".io")
            source.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                source.write_bytes(path.read_bytes())
            self.source_archive = source
            self.archive = IOArchive(source)
            self.assembly = self.archive.assembly
            embedded_packages(path, self.directory / "packages/import")
            self.catalog = NativeCatalog(self.catalog_root, [self.directory / "packages/import"])
        else:
            self.assembly = Assembly.read(path)
            self.archive, self.source_archive = None, None
            self.catalog = NativeCatalog(self.catalog_root)
        self._embedded_geometry()
        self.dependencies()
        self.save()

    def call(self, name, arguments):
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ValueError("Unknown assembly tool or invalid arguments")
        with self.lock:
            before = self.assembly.revision
            checkpoint = deepcopy(self.assembly)
            bindings = deepcopy(self.module_bindings)
            catalog = self.catalog
            control_error = None
            diagnostic = None
            try:
                from .assembly_protocol import validate_call
                try:
                    validate_call(name, arguments)
                except ValidationError as error:
                    diagnostic = validation_diagnostic(name, arguments, error)
                    raise
                result = getattr(self, "tool_" + name)(**arguments)
                protected = getattr(self, "protected_state", None)
                if protected:
                    from .assembly_mounts import protected_changed
                    graph = self.assembly.graph()
                    if protected_changed(graph, self.module_bindings, protected):
                        raise ValueError("Completed module changed; edit was rolled back. Preserve completed bindings "
                            + ", ".join(sorted(protected["bindings"]))
                            + ". Create an independent submodel instance with edit(module/add) or graft for the "
                            "current task. Rebuilding completed modules requires host scheduler intervention.")
                if self.assembly.revision != before:
                    self.save()
                record = dict(protocol=PROTOCOL, tool=name, arguments=arguments, revision_before=before,
                              revision_after=self.assembly.revision, ok=True, result=result)
            except Exception as error:
                self.assembly, self.catalog = checkpoint, catalog
                self.module_bindings = bindings
                record = dict(protocol=PROTOCOL, tool=name, arguments=arguments, revision_before=before,
                    revision_after=self.assembly.revision, ok=False, error=f"{type(error).__name__}: {error}")
                if diagnostic is not None:
                    record["diagnostic"] = diagnostic
                if isinstance(error, AssemblyControlError):
                    control_error = error
            with (self.directory / "trajectory.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if control_error is not None:
                raise control_error
            return record

    def tool_inspect(self, offset=0, limit=50):
        return dict(graph=compact(self.assembly.graph(), offset, limit), bom=self.assembly.bom())

    def tool_remember(self, text):
        write_json(getattr(self, "note_path", self.directory / "planner-note.json"), dict(text=text))
        return dict(text=text)

    def tool_context(self, resource, pointer="", offset=0, limit=400):
        if resource not in self.context_resources and ":/" in resource:
            resource, embedded = resource.rsplit(":/", 1)
            embedded = "/" + embedded
            if pointer and pointer != embedded:
                raise ValueError("Conflicting context pointers")
            pointer = embedded
        value = self.context_resources[resource]
        if callable(value):
            value = value()
        if pointer and not pointer.startswith("/"):
            raise ValueError("Use an RFC 6901 JSON pointer")
        for key in pointer.split("/")[1:]:
            if re.search(r"~(?![01])", key):
                raise ValueError("Invalid JSON pointer escape")
            key = key.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise ValueError("Invalid JSON pointer array index")
                value = value[int(key)]
            else:
                value = value[key]
        # Character pages preserve exact JSON, including complete matrices and IDs.
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if offset > len(content):
            raise ValueError("Context offset exceeds resource length")
        end = min(len(content), offset + limit)
        return dict(resource=resource, pointer=pointer, offset=offset, total=len(content),
                    text=content[offset:end], next_offset=end if end < len(content) else None)

    def tool_query_part(self, part):
        from .assembly_inspection import part_bounds
        record = self.catalog.part(part)
        try:
            record["native_geometry"] = part_bounds(self.catalog, part)
        except (ValueError, OSError) as error:
            record["errors"].append(str(error))
            record["geometry"] = False
            record["status"] = "unverified"
        return record

    def tool_query_module(self, module_id):
        from .assembly_inspection import inspect_module
        return inspect_module(self, module_id)

    def tool_search_parts(self, query, limit=20):
        return self.catalog.search(query, limit)

    def tool_view_reference(self, view):
        from .common import digest
        path, expected = self.reference_images[view]
        if digest(path) != expected:
            raise ValueError("Reference image changed during assembly")
        return dict(view=view, file=str(path), sha256=expected, synthetic=True,
                    vision_delivery="planner_capability_dependent")

    def tool_retrieve(self, **arguments):
        if self.corpus is None:
            raise ValueError("Module corpus not configured")
        if "exclude_models" in arguments:
            raise ValueError("Evaluation exclusions cannot be changed by planner")
        modules = self.corpus.retrieve(exclude_models=self.exclude_models,
            exclude_families=self.exclude_families, exclude_hashes=self.exclude_hashes, **arguments)
        result = dict(snapshot=self.corpus.snapshot(), modules=modules, filters=arguments)
        if not modules:
            result["diagnostics"] = self.corpus.retrieval_diagnostics(arguments.get("types", ()))
        return result

    def tool_edit(self, revision, operations, strict=False):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 256:
            raise ValueError("Provide 1 to 256 operations")
        result = self.assembly.transact(revision, operations, (lambda g: validate(g, self.catalog)) if strict else None)
        return {**compact(result), **{key: result[key] for key in ("aliases", "clones") if key in result}}

    def tool_graft(self, revision, module_id, matrix, parent="root", color=16):
        if self.corpus is None or not rigid(matrix):
            raise ValueError("Graft needs a corpus and rigid matrix")
        item = self.corpus.module(module_id)
        if not self.corpus.allowed(item, self.exclusions):
            raise ValueError("Held-out family or duplicate module cannot be retrieved or grafted")
        module = Assembly.read(self.corpus.directory / item["metadata"]["file"])
        before = deepcopy((self.assembly.sections, self.assembly.joints, self.assembly.revision))
        try:
            prefix = "mod-" + module_id + "-"
            mapping = {name: prefix + name for name in module.sections}
            for name, rows in module.sections.items():
                target = mapping[name]
                content = deepcopy(rows)
                for row in content:
                    ref = reference(row["line"])
                    if ref and ref["name"] in mapping:
                        ref["name"] = mapping[ref["name"]]
                        row["line"] = reference_line(ref)
                self.assembly.sections[target] = content
            graph = self.assembly.transact(revision, [dict(op="add", parent=parent, part=mapping[module.root], matrix=matrix, color=color)])
            package = self.corpus.directory / item["metadata"]["package"]
            if package.is_dir():
                import shutil
                destination = self.directory / "packages" / item["metadata"]["source_sha256"]
                shutil.copytree(package, destination, dirs_exist_ok=True)
                self.catalog = NativeCatalog(self.catalog_root, list(dict.fromkeys([*self.catalog.packages, destination])))
            self._embedded_geometry()
            self.dependencies()
            return dict(graph=compact(graph), module_hash=item["hash"], source_model=item["model_id"],
                        source_check=item["check_status"], semantic_tag_status="unverified")
        except Exception:
            self.assembly.sections, self.assembly.joints, self.assembly.revision = before
            raise

    def tool_search_mounts(self, **arguments):
        from .assembly_search import search_mounts
        return search_mounts(self, **arguments)

    def tool_axial_clearance(self, **arguments):
        from .assembly_axial import axial_clearance
        return axial_clearance(self.assembly.graph(), self.catalog, **arguments, checkpoint=self.checkpoint)

    def tool_axial_motion(self, **arguments):
        from .assembly_axial import axial_motion
        return axial_motion(self.assembly.graph(), self.catalog, **arguments, checkpoint=self.checkpoint)

    def tool_snap(self, revision, source, source_port, target, target_port, strict=False, moving=None):
        graph = self.assembly.graph()
        nodes = {n["id"]: n for n in graph["instances"]}
        if source == target or source.startswith(target + "/") or target.startswith(source + "/"):
            raise ValueError("Snap requires independent instances")
        a, b = nodes[source], nodes[target]
        if a["kind"] != "part" or b["kind"] != "part":
            raise ValueError("Select concrete connector-bearing parts")
        moving = source if moving is None else moving
        if moving not in nodes or (source != moving and not source.startswith(moving + "/")):
            raise ValueError("Moving instance must be the source or its ancestor module")
        if target == moving or target.startswith(moving + "/"):
            raise ValueError("Snap target must be outside the moving subtree")
        pa = next(p for p in self.catalog.ports(a["part"]) if p.id == source_port)
        pb = next(p for p in self.catalog.ports(b["part"]) if p.id == target_port)
        if not compatible(pa, pb):
            raise ValueError("Incompatible connector types")
        wa, wb = np.array(a["world"]), np.array(b["world"])
        original = wa.copy()
        da, db = wa[:3, :3] @ pa.direction, wb[:3, :3] @ pb.direction
        desired = -db if pa.kind == pb.kind == "axle" and da @ db < 0 else db
        cross, dot = np.cross(da, desired), np.clip(da @ desired, -1, 1)
        if np.linalg.norm(cross) < 1e-8:
            if dot > 0:
                rotation = np.eye(3)
            else:
                seed = np.eye(3)[np.argmin(abs(da))]
                axis = np.cross(da, seed)
                rotation = Rotation.from_rotvec(axis / np.linalg.norm(axis) * np.pi).as_matrix()
        else:
            rotation = Rotation.from_rotvec(cross / np.linalg.norm(cross) * np.arccos(dot)).as_matrix()
        wa[:3, :3] = rotation @ wa[:3, :3]
        target_origin = wb[:3, 3] + wb[:3, :3] @ pb.position
        if pa.kind == pb.kind == "axle":
            target_origin = shaft_snap_origin(
                (source, pa, wa[:3, 3] + wa[:3, :3] @ pa.position,
                 wa[:3, :3] @ pa.direction, wa[:3, :3] @ pa.forward),
                (target, pb, target_origin, db, wb[:3, :3] @ pb.forward))
        wa[:3, 3] = target_origin - wa[:3, :3] @ pa.position
        moved = nodes[moving]
        parent_world = np.eye(4) if moved["parent"] == "root" else np.array(nodes[moved["parent"]]["world"])
        delta = wa @ np.linalg.inv(original)
        operations = [dict(op="transform", instance=moving,
            matrix=(np.linalg.inv(parent_world) @ delta @ np.asarray(moved["world"])).tolist())]
        if moving in self.assembly.joints:
            joint = deepcopy(self.assembly.joints[moving])
            parent_delta = np.linalg.inv(parent_world) @ delta @ parent_world
            joint["axis"] = (parent_delta[:3, :3] @ np.asarray(joint["axis"])).tolist()
            joint["origin"] = (parent_delta @ [*joint.get("origin", [0, 0, 0]), 1])[:3].tolist()
            operations.append(joint)
        result = self.tool_edit(revision, operations, strict=strict)
        from .assembly_catalog import connections
        contacts, _ = connections(self.assembly.graph()["instances"], self.catalog)
        if not any({c["a"], c["b"]} == {source, target} for c in contacts):
            raise ValueError("Snap did not produce a verified connector contact")
        return result

    def tool_attach(self, revision, part, source_port, target, target_port,
                    parent="root", color=16, matrix=None, strict=True):
        # Reuse native snapping, but publish one revision and no intermediate checkpoint.
        before = deepcopy(self.assembly)
        try:
            created = self.tool_edit(revision, [dict(op="add", alias="attached", part=part,
                parent=parent, color=color, matrix=np.eye(4).tolist() if matrix is None else matrix)])
            source = created["aliases"]["attached"]
            self.tool_snap(self.assembly.revision, source, source_port, target, target_port)
            if strict:
                check = validate(self.assembly.graph(), self.catalog)
                if check.get("status") != "pass":
                    raise ValueError("Attachment requires a complete native check: " + json.dumps(check))
            self.assembly.revision = revision + 1
            return dict(instance=source, target=target, source_port=source_port, target_port=target_port,
                revision=self.assembly.revision, graph=compact(self.assembly.graph()))
        except Exception:
            self.assembly = before
            raise

    def tool_articulate(self, revision, instance, source, source_port, target, target_port,
                        limits, purpose, type="revolute"):
        from .assembly_catalog import connections
        from .assembly_motion import joint_range_issues
        graph = self.assembly.graph()
        if revision != graph["revision"]:
            raise ValueError("Stale assembly revision")
        nodes = {node["id"]: node for node in graph["instances"]}
        if instance not in nodes or source not in nodes or target not in nodes:
            raise ValueError("Unknown joint instance or connector part")
        inside = lambda identity: identity == instance or identity.startswith(instance + "/")
        if not inside(source) or inside(target):
            raise ValueError("Joint source must be inside moving subtree and target outside it")
        if nodes[source]["kind"] != "part" or nodes[target]["kind"] != "part":
            raise ValueError("Select concrete connector-bearing parts")
        pa = next(p for p in self.catalog.ports(nodes[source]["part"]) if p.id == source_port)
        pb = next(p for p in self.catalog.ports(nodes[target]["part"]) if p.id == target_port)
        if pa.kind != pb.kind or pa.kind not in {"axle", "hinge"} or type == "prismatic" and pa.kind != "axle":
            raise ValueError("Joint requires native axle/hinge connectors of the same kind")
        contacts, _ = connections(graph["instances"], self.catalog)
        selected = next((contact for contact in contacts
            if (contact["a"], contact["port_a"], contact["b"], contact["port_b"]) in {
                (source, source_port, target, target_port), (target, target_port, source, source_port)}), None)
        if selected is None:
            raise ValueError("Selected ports have no verified native contact; snap the subtree first")
        parent = nodes[instance]["parent"]
        parent_world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
        target_local = np.linalg.inv(parent_world) @ np.asarray(nodes[target]["world"])
        joint = dict(op="joint", instance=instance, type=type, purpose=purpose, limits=limits,
            axis=(target_local[:3, :3] @ pb.direction).tolist(),
            origin=(target_local @ [*pb.position, 1])[:3].tolist())
        issues = joint_range_issues(joint)
        if issues:
            raise ValueError("Invalid joint range: " + ", ".join(issues))
        self.tool_edit(revision, [joint])
        return dict(revision=self.assembly.revision, instance=instance, joint=joint, contact=selected,
            motion="unverified", graph=compact(self.assembly.graph()))

    def tool_build(self, revision, operations, strict=True):
        if revision != self.assembly.revision:
            raise ValueError("Stale assembly revision")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
            raise ValueError("Provide 1 to 64 build operations")
        before, aliases, clones = deepcopy(self.assembly), {}, []
        try:
            for index, original in enumerate(operations):
                op = deepcopy(original)
                action, alias = op.pop("op"), op.pop("alias", None)
                if alias is not None and (action not in {"module", "add", "attach", "clone"}
                        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", alias) or alias in aliases):
                    raise ValueError("Invalid or duplicate build alias")
                for key in ("parent", "instance", "source", "target", "moving"):
                    if op.get(key, "").startswith("@"):
                        name = op[key][1:]
                        if name not in aliases:
                            raise ValueError("Unresolved build alias: " + name)
                        op[key] = aliases[name]
                current = self.assembly.revision
                if action == "attach":
                    result = self.tool_attach(current, **op, strict=False)
                    if alias is not None:
                        aliases[alias] = result["instance"]
                elif action == "snap":
                    self.tool_snap(current, **op)
                elif action == "articulate":
                    self.tool_articulate(current, **op)
                else:
                    result = self.tool_edit(current, [dict(op=action, **op,
                        **({"alias": alias} if alias is not None else {}))])
                    aliases.update(result.get("aliases", {}))
                    clones.extend(result.get("clones", []))
            check = validate(self.assembly.graph(), self.catalog) if strict else None
            if check is not None and check.get("status") != "pass":
                raise ValueError("Build requires a complete native check: " + json.dumps(check))
            self.assembly.revision = revision + 1
            return dict(revision=self.assembly.revision, aliases=aliases, clones=clones, operations=len(operations),
                native_status=check["status"] if check is not None else "unverified",
                graph=compact(self.assembly.graph()))
        except Exception as error:
            self.assembly = before
            raise ValueError(f"Build rolled back at operation {index}: {error}") from error

    def tool_check(self):
        graph = self.assembly.graph()
        report = validate(graph, self.catalog)
        from .assembly_layout import wheel_layout
        layout = wheel_layout(graph, self.catalog, self.vehicle_contract, self.module_bindings)
        report["wheel_layout"] = layout
        if layout["applicable"]:
            report["gates"]["wheel_layout"] = layout["status"]
            if layout["status"] == "fail":
                report["status"] = "fail"
            elif layout["status"] != "pass" and report["status"] != "fail":
                report["status"] = "unverified"
        if len(report["components"]) > 1:
            from .assembly_catalog import shaft_diagnostics
            report["shaft_diagnostics"] = shaft_diagnostics(graph["instances"], self.catalog, report["components"])
        from .bricklayer_audit import audit_ldraw
        flat = [reference_line(dict(name=n["part"], color=n["color"], matrix=n["world"]))
                for n in graph["instances"] if n["kind"] == "part"]
        audit_source = self.directory / "bricklayer-input.ldr"
        audit_source.write_text("0 Independent native assembly check\n" + "\n".join(flat) + "\n", encoding="utf-8")
        independent = audit_ldraw(audit_source, self.directory / "bricklayer-audit.json", library=self.catalog.root)
        report["bricklayer"] = independent
        independent["native_status"] = report["status"]
        independent["disagreement"] = independent["enabled"] and independent["status"] != report["status"]
        if independent.get("complete"):
            parts = [n for n in graph["instances"] if n["kind"] == "part"]
            for index, problem in independent.get("problem_parts", {}).items():
                problem["instance"] = parts[int(index)]["id"]
        write_json(self.directory / "bricklayer-audit.json", independent)
        if independent["enforced"]:
            report["gates"]["bricklayer"] = independent["status"]
            if independent["status"] == "fail":
                report["status"] = "fail"
            elif independent["status"] != "pass" and report["status"] != "fail":
                report["status"] = "unverified"
        report.update(self.verification_inputs())
        write_json(self.directory / "checks.json", report)
        return report

    def verification_inputs(self):
        from .assembly_evidence import snapshot_files
        from .assembly_requirements import identity
        graph = self.assembly.graph()
        paths = set(self.catalog.library.used)
        for part in {n["part"] for n in graph["instances"] if n["kind"] == "part"}:
            for folder, suffix in (("connectivity", ".conn"), ("collider", ".col")):
                try:
                    paths.add(self.catalog.evidence(part, folder, suffix))
                except (ValueError, OSError):
                    pass  # The checker records missing evidence as unverified.
        paths.update((self.catalog.root / "LDConfig.ldr",
            self.catalog.root.parent / "data/StudioColorDefinition.txt"))
        paths.update((self.directory / "bricklayer-input.ldr", self.directory / "bricklayer-audit.json",
            Path(__file__).with_name("bricklayer_audit.py")))
        return dict(graph_sha256=identity(graph), evidence_files=snapshot_files(paths))

    def tool_bind_module(self, revision, module, roots, roles):
        from .assembly_requirements import validate_binding
        if revision != self.assembly.revision:
            raise ValueError("Stale assembly revision")
        if not self.vehicle_contract:
            raise ValueError("No host vehicle contract; use ordinary module tools for non-vehicle tasks")
        binding = validate_binding(self.assembly.graph(), self.vehicle_contract, module, roots, roles,
            bindings=self.module_bindings)
        if self.module_bindings.get(module) == binding:
            return dict(module=module, binding=binding, semantic_status="unverified",
                revision=self.assembly.revision, changed=False)
        self.module_bindings[module] = binding
        self.assembly.revision += 1
        return dict(module=module, binding=binding, semantic_status="unverified", revision=self.assembly.revision)

    def tool_module_progress(self):
        from .assembly_requirements import evaluate
        def read(name):
            path = self.directory / name
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        review = read("module-role-review.json")
        from .assembly_evidence import files_current, snapshot_files
        evidence = review.get("evidence", [])
        verified = bool(evidence) and files_current(review.get("native_evidence_files"))
        for item in evidence:
            path = (self.directory / item.get("file", "")).resolve()
            verified = verified and path.is_relative_to(self.directory) and path.is_file()
            if verified:
                verified = sha256(path.read_bytes()).hexdigest() == item.get("sha256")
        report = evaluate(self.assembly.graph(), self.catalog, self.vehicle_contract, self.module_bindings,
            read("checks.json"), read("motion.json"), {**review, "evidence_verified": verified})
        reviewed_files = [(self.directory / item.get("file", "")).resolve() for item in evidence]
        report["evidence_files"] = snapshot_files([
            self.directory / name for name in ("state.json", "checks.json", "motion.json", "module-role-review.json")
        ] + [item["file"] for item in self.verification_inputs()["evidence_files"]]
          + [path for path in reviewed_files if path.is_relative_to(self.directory)])
        write_json(self.directory / "module-progress.json", report)
        return report

    def tool_candidate(self, revision, module, label):
        from .assembly_candidates import capture
        return capture(self, revision, module, label)

    def tool_restore_candidate(self, revision, module, candidate):
        from .assembly_candidates import restore
        return restore(self, revision, module, candidate)

    def review_vehicle_modules(self):
        from .assembly_module_review import review
        return review(self)

    def tool_motion(self, samples=9):
        from .assembly_motion import evaluate_motion
        graph = self.assembly.graph()
        report = evaluate_motion(graph, lambda graph: validate(graph, self.catalog, collision_limit=10),
            samples, checkpoint=self.checkpoint)
        if self.vehicle_contract:
            from .assembly_mounts import mechanism_response
            report["mechanism_response"] = {name: mechanism_response(graph, self.vehicle_contract,
                self.module_bindings, name, require_complete=True) for name in ("steering", "suspension")
                if name in self.vehicle_contract["modules"]}
            if any(row["status"] != "pass" for row in report["mechanism_response"].values()):
                report["status"] = "fail"
        report.update(self.verification_inputs())
        write_json(self.directory / "motion.json", report)
        return report

    def tool_render(self):
        import trimesh
        from trimesh.visual.material import PBRMaterial
        from .assembly_materials import linear_color_factors, palette, rgba
        colors_native = palette(self.catalog_root)
        scene, reusable, missing, unknown_colors = trimesh.Scene(), {}, [], set()
        frame = np.diag([.0004, -.0004, -.0004, 1])
        for node in self.assembly.graph()["instances"]:
            if node["kind"] != "part":
                continue
            try:
                triangles, colors = self.catalog.library.triangles(node["part"])
                for color in np.unique(colors):
                    paint = node["color"] if color == 16 else int(color)
                    key = (node["part"], int(color), paint)
                    if key not in reusable:
                        tint = rgba(paint, colors_native)
                        if tint is None:
                            unknown_colors.add(paint)
                            tint = [130, 130, 130, 255]
                        selected = triangles[colors == color]
                        mesh = trimesh.Trimesh(vertices=selected.reshape(-1, 3),
                            faces=np.arange(len(selected) * 3).reshape(-1, 3), process=False)
                        mesh.visual = trimesh.visual.TextureVisuals(material=PBRMaterial(baseColorFactor=tint,
                            metallicFactor=.03, roughnessFactor=.12 if tint[3] < 255 else .3,
                            alphaMode="BLEND" if tint[3] < 255 else "OPAQUE", doubleSided=True))
                        name = f"g{len(reusable)}"
                        scene.geometry[name] = mesh
                        reusable[key] = name
                    scene.graph.update(frame_to=node["id"] + ":" + str(color),
                        matrix=frame @ np.asarray(node["world"]), geometry=reusable[key])
            except (ValueError, OSError) as e:
                missing.append(dict(instance=node["id"], issue=str(e)))
        path = self.directory / "assembly.glb"
        if not scene.geometry:
            raise ValueError("No resolved geometry to render")
        path.write_bytes(scene.export(file_type="glb", tree_postprocessor=linear_color_factors))
        return dict(file=path.name, missing=missing, unknown_colors=sorted(unknown_colors),
            geometry_warnings=[*self.catalog.library.ignored_edge_records.values(),
                *self.catalog.library.geometry_warnings.values()],
            status="unverified" if missing or unknown_colors else "pass", appearance="unverified")

    def tool_preview(self):
        from .assembly_presentation import preview
        from .assembly_requirements import identity
        from .common import digest
        rendered = self.tool_render()
        if rendered["status"] != "pass":
            raise ValueError("Preview requires complete native geometry and colours: " + json.dumps(rendered))
        graph = self.assembly.graph()
        result = preview(self.directory / rendered["file"], self.directory / "previews")
        result = {**result, "revision": graph["revision"], "graph_sha256": identity(graph),
            "native_dependencies": self.catalog.library.dependency_record(),
            "status": "pass", "appearance": "unverified"}
        from .assembly_evidence import snapshot_files
        result["evidence_files"] = snapshot_files([item["file"] for item in result["evidence_files"]]
            + [item["file"] for item in result["native_dependencies"]["files"]]
            + [self.directory / rendered["file"], self.catalog.root / "LDConfig.ldr",
                self.catalog.root.parent / "data/StudioColorDefinition.txt"])
        sheet = Path(result["directory"]) / "contact-sheet.png"
        result["image"] = dict(file=str(sheet), sha256=digest(sheet))
        write_json(self.directory / "preview.json", result)
        return result

    def tool_export(self):
        graph = self.assembly.graph()
        if not any(n["kind"] == "part" for n in graph["instances"]):
            raise ValueError("Cannot export an empty assembly")
        content = self.assembly.text()
        (self.directory / "model.mpd").write_text(content, encoding="utf-8")
        flat = [reference_line(dict(name=n["part"], color=n["color"], matrix=n["world"]))
                for n in graph["instances"] if n["kind"] == "part"]
        (self.directory / "model.ldr").write_text("\n".join(flat)+"\n", encoding="utf-8")
        write_json(self.directory / "assembly-graph.json", graph)
        write_json(self.directory / "bom.json", self.assembly.bom())
        write_json(self.directory / "steps.json", dict(schema="assembly-steps.v1", steps=[
            dict(module=p, step=s, instances=[n["id"] for n in graph["instances"] if n["parent"] == p and n["step"] == s])
            for p,s in sorted({(n["parent"],n["step"]) for n in graph["instances"]})]))
        report = self.tool_check()
        independent = report.get("bricklayer", {})
        if independent.get("enforced") and (independent.get("status") != "pass" or not independent.get("complete")):
            raise ValueError("Independent BrickLayer audit blocked Studio export; inspect bricklayer-audit.json")
        destination = self.directory / "model.io"
        if self.archive is not None:
            self.archive.assembly = self.assembly
            archive_report = self.archive.export(destination, dependencies=self.dependencies())
        else:
            temporary = destination.with_suffix(".io.tmp")
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as z:
                z.writestr("model.ldr", content)
                z.writestr("model2.ldr", content)
                z.writestr(".info", json.dumps(dict(total_parts=sum(x["quantity"] for x in self.assembly.bom()), application="Car2LGEO_3D_LLM")))
                z.writestr("car2lego/assembly-graph.json", json.dumps(graph))
                for name, data in self.dependencies().items():
                    z.writestr("CustomParts/" + name, data)
            restored = IOArchive(temporary).assembly
            if restored.bom() != self.assembly.bom() or restored.graph()["instances"] != graph["instances"]:
                raise ValueError("IO graph roundtrip mismatch")
            temporary.replace(destination)
            archive_report = dict(archive_roundtrip_verified=True, studio_open_verified=False)
        dependencies = self.dependencies()
        write_json(self.directory / "dependencies.json", dict(schema="assembly-dependencies.v1",
            io_sha256=sha256(destination.read_bytes()).hexdigest(),
            files=[dict(path=name, sha256=sha256(data).hexdigest(), bytes=len(data)) for name, data in sorted(dependencies.items())],
            installation="Install included CustomParts through Studio/PartDesigner; verify geometry and connections before use.",
            connection_status="see checks.json; missing data remains unverified"))
        with ZipFile(self.directory / "custom-parts.zip", "w", compression=ZIP_DEFLATED) as package:
            for name, data in sorted(dependencies.items()):
                package.writestr("CustomParts/" + name, data)
            package.writestr("dependencies.json", (self.directory / "dependencies.json").read_bytes())
            package.writestr("INSTALL.txt", "Custom dependencies use original LDraw/LDU dimensions.\n"
                "Install CustomParts with Studio/PartDesigner. Review checks.json for missing connection evidence.\n"
                "This package does not certify physical buildability.\n")
        return dict(file=destination.name, checks=report, archive=archive_report, delivery_status="draft",
            module_progress=self.tool_module_progress())
