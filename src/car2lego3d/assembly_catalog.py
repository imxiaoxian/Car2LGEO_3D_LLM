"""Native, source-hashed geometry/connector/collider evidence without Studio."""
from dataclasses import asdict
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from hashlib import sha256
from itertools import chain
import os
from pathlib import Path

import numpy as np
import re
from scipy.spatial import cKDTree

from .assembly_graph import rigid
from .ldraw_document import safe_name
from .native_ports import parse_connections
from .part_library import PartLibrary

AXLE_PAIRS = {frozenset(p) for p in (("pin_socket", "pin"), ("pin_socket", "pin_solid"),
    ("pin_socket", "axle"), ("pin_socket", "axle_guard"), ("round_hole", "pin"),
    ("round_hole", "pin_solid"), ("round_hole", "axle"), ("round_hole", "axle_guard"),
    ("axle_socket", "axle"), ("axle_socket", "axle_guard"),
    ("round_hole", "round_bar"), ("clip", "bar"), ("clip", "bar_socket"),
    ("small_pin_socket", "small_pin"), ("bar_socket", "small_pin"))}


@lru_cache(maxsize=65536)
def description_record(folder, name, size, modified):
    with (Path(folder) / name).open(encoding="utf-8-sig", errors="replace") as stream:
        return stream.readline().strip().removeprefix("0 ")


@lru_cache(maxsize=64)
def description_records(folder, versions):
    def read(version):
        return version[0], description_record(folder, *version)

    # Bound native-library I/O while allowing independent Windows file reads.
    if len(versions) < 32:
        return dict(map(read, versions))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(read, versions))


@lru_cache(maxsize=8192)
def collider_records(data):
    result = []
    for line in data.decode("utf-8-sig").splitlines():
        fields = line.split()
        if len(fields) >= 17 and fields[0] == "9":
            axes = np.array(fields[2:11], float).reshape(3, 3)
            center, half = np.array(fields[11:14], float), np.array(fields[14:17], float)
            if not np.isfinite(np.r_[axes.ravel(), center, half]).all() or np.any(half < 0):
                raise ValueError("Invalid collider")
            if not np.allclose(axes.T @ axes, np.eye(3), atol=.001):
                raise ValueError("Nonrigid collider")
            for array in (center, axes, half):
                array.flags.writeable = False
            result.append((center, axes, half))
    if not result:
        raise ValueError("Unsupported collider")
    return tuple(result)


def compatible(a, b):
    if {a.kind, b.kind} == {"stud", "hole"}:
        stud, hole = (a, b) if a.kind == "stud" else (b, a)
        allowed = {-2: None, 10: {10, 20}, 20: {10, 20, 30}, 3: {20, 25}, 40: {40}, 50: {50}, 60: {20}}
        return (stud.area >= 4 and hole.area >= 4 and stud.altitude in allowed
                and (allowed[stud.altitude] is None or hole.altitude in allowed[stud.altitude]))
    if a.kind == b.kind == "axle":
        return (frozenset((a.semantic, b.semantic)) in AXLE_PAIRS
                and (not a.grabbing_required or b.grabbing) and (not b.grabbing_required or a.grabbing))
    if a.kind == b.kind and a.kind in {"fixed", "hinge"}:
        return a.subtype // 2 == b.subtype // 2 and a.subtype % 2 != b.subtype % 2 and a.tag == b.tag
    return False


def shaft_match(a, b, tolerance=.25):
    _, pa, oa, da, fa = a
    _, pb, ob, db, fb = b
    dot = float(da @ db)
    if abs(dot) < .9999 or not compatible(pa, pb):
        return False
    delta, offset = ob - oa, float((ob - oa) @ da)
    if np.linalg.norm(delta - offset * da) > tolerance:
        return False
    la = pa.length - (2 if pa.semantic == "axle_guard" and pb.semantic != "pin_socket" else 0)
    lb = pb.length - (2 if pb.semantic == "axle_guard" and pa.semantic != "pin_socket" else 0)
    alo, ahi = -la, 0.
    blo, bhi = sorted((offset, offset - np.sign(dot) * lb))
    required = 4 - tolerance if "pin_socket" in (pa.semantic, pb.semantic) and "pin" not in (pa.semantic, pb.semantic) else tolerance
    if min(la, lb) <= 0 or min(ahi, bhi) - max(alo, blo) < required:
        return False
    for point, cap, low, high in ((0, pa.start_capped, blo, bhi), (-la, pa.end_capped or pa.semantic == "axle_guard", blo, bhi),
                                  (offset, pb.start_capped, alo, ahi), (offset - np.sign(dot) * lb, pb.end_capped or pb.semantic == "axle_guard", alo, ahi)):
        if cap and low + tolerance < point < high - tolerance:
            return False
    cosine = abs(float(fa @ fb))
    return not (pa.cross_type and pb.cross_type and min(abs(cosine), abs(cosine - 1)) > 1e-4)


def shaft_snap_origin(a, b):
    """Seat aligned shafts using their intervals and stops, preserving keyed roll."""
    _, pa, oa, da, fa = a
    _, pb, ob, db, fb = b
    dot = float(da @ db)
    if abs(dot) < .9999 or not compatible(pa, pb):
        raise ValueError("Shaft axes and keyed orientation must be aligned before seating")
    la = pa.length - (2 if pa.semantic == "axle_guard" and pb.semantic != "pin_socket" else 0)
    lb = pb.length - (2 if pb.semantic == "axle_guard" and pa.semantic != "pin_socket" else 0)
    low, high = sorted((0., -np.sign(dot) * la))
    current = float((oa - ob) @ db)
    # Maximal overlap occurs at aligned interval endpoints. Check every stop with
    # the same matcher used by validation; coincident origins alone are insufficient.
    offsets = {current, -lb - low, -lb - high, -low, -high}
    candidates = []
    for offset in sorted(offsets):
        origin = ob + offset * db
        moved = (a[0], pa, origin, da, fa)
        if shaft_match(moved, b):
            overlap = min(offset + high, 0.) - max(offset + low, -lb)
            candidates.append((-overlap, float(np.linalg.norm(origin - oa)), offset, origin))
    if not candidates:
        raise ValueError("No legal shaft seating; check insertion stops and keyed orientation")
    return min(candidates, key=lambda item: item[:3])[3]


class NativeCatalog:
    def __init__(self, root, packages=()):
        self.root = Path(root)
        self.packages = tuple(Path(p) for p in packages)
        self.library = PartLibrary(root, packages)

    def descriptions(self):
        records = {}
        for root in (*self.packages, self.root, self.root / "UnOfficial"):
            folder = root / "parts"
            if not folder.is_dir():
                continue
            with os.scandir(folder) as entries:
                versions = []
                for entry in entries:
                    if entry.name.lower().endswith(".dat") and entry.is_file():
                        stat = entry.stat()
                        versions.append((entry.name, stat.st_size, stat.st_mtime_ns))
            for name, description in description_records(str(folder.resolve()), tuple(sorted(versions))).items():
                records.setdefault(name, description)
        return records

    def search(self, query, limit=20):
        terms = re.findall(r"[^\W_]+(?:\.\d+)?", query.casefold())
        if not terms or not 1 <= limit <= 50:
            raise ValueError("Provide a part description or filename")
        numbers = {term: Decimal(term) for term in terms if re.fullmatch(r"\d+(?:\.\d+)?", term)}
        words = [term for term in terms if term not in numbers]
        number_lookup = len(terms) == 1 and terms[0].isdigit()
        filename = query.strip().casefold() if re.fullmatch(r"[^\s/\\]+\.dat", query.strip(), re.I) else None
        spellings = {"tire": "tyre", "tyre": "tyre", "centre": "center", "center": "center"}
        literal, expanded = [], []
        for name, description in self.descriptions().items():
            item = dict(part=name, description=description)
            if filename:
                if name.casefold() == filename:
                    literal.append(item)
                continue
            text = (name + " " + description).casefold()
            tokens = set(re.findall(r"[^\W_]+", text))
            description_tokens = set(re.findall(r"[^\W_]+", description.casefold()))
            normalized = {spellings.get(term, term) for term in tokens}
            open_beam = {"technic", "beam", "open", "center"} <= {
                spellings.get(term, term) for term in description_tokens}
            # Expand only complete catalog words; filenames and dimensions stay exact.
            direct = all(term in tokens if term in spellings or term == "frame" else term in text for term in words)
            if not direct and not all(
                spellings[term] in normalized if term in spellings else
                ("frame" in tokens or open_beam) if term == "frame" else term in text for term in words
            ):
                continue
            # Description dimensions must not match unrelated digits in a part ID.
            dimensions = {Decimal(value) for value in re.findall(r"\d+(?:\.\d+)?", description)} if numbers else set()
            stem = Path(name).stem.casefold()
            if all(value in dimensions or term == stem or (number_lookup and term in name.casefold())
                   for term, value in numbers.items()):
                (literal if direct else expanded).append(item)
        found = literal + expanded
        return dict(query=query, total=len(found), parts=found[:limit],
                    geometry_and_connection_status="query_part_required")

    def evidence(self, part, folder, suffix, _aliases=()):
        name = safe_name(part)
        if name in _aliases:
            raise ValueError('Cyclic native evidence alias: ' + name)
        relative = Path(name).with_suffix(suffix)
        for root in (*self.packages, self.root, self.root / "UnOfficial"):
            for path in (root / folder / relative, root / folder / relative.name):
                if path.is_file():
                    return path
        alias = self.library.identity_alias(name)
        if alias is not None:
            return self.evidence(alias, folder, suffix, (*_aliases, name))
        raise ValueError(f"Missing {folder} data: {part}")

    def ports(self, part):
        return parse_connections(self.evidence(part, "connectivity", ".conn").read_bytes())

    def boxes(self, part):
        # Key by actual bytes so same-size edits and restored timestamps invalidate.
        return collider_records(self.evidence(part, "collider", ".col").read_bytes())

    def part(self, name):
        name = safe_name(name)
        record = dict(schema="part-catalog.v1", part=name, geometry=False, connectors=False, collider=False,
                      origin="native LDraw origin; no heuristic centre correction", hashes={}, evidence_sources={}, errors=[])
        try:
            geometry = self.library.resolve(name)
            record["hashes"]["geometry"] = sha256(geometry.read_bytes()).hexdigest()
            record["evidence_sources"]["geometry"] = str(geometry)
            record["geometry"] = True
        except (FileNotFoundError, ValueError) as e:
            record["errors"].append(str(e))
        for key, folder, suffix, loader in (("connectors", "connectivity", ".conn", self.ports),
                                             ("collider", "collider", ".col", self.boxes)):
            try:
                path = self.evidence(name, folder, suffix)
                data = loader(name)
                record[key] = True
                record["hashes"][key] = sha256(path.read_bytes()).hexdigest()
                record["evidence_sources"][key] = str(path)
                if key == "connectors":
                    record["ports"] = [asdict(p) for p in data]
                    record["unsupported_kinds"] = sorted({p.kind for p in data} - {"stud", "hole", "axle", "fixed", "hinge"})
            except (ValueError, OSError) as e:
                record["errors"].append(str(e))
        record["status"] = "pass" if all(record[k] for k in ("geometry", "connectors", "collider")) and not record.get("unsupported_kinds") else "unverified"
        return record


def world_ports(nodes, catalog):
    result, unknown = [], []
    for n in nodes:
        m = np.asarray(n["world"])
        try:
            ports = catalog.ports(n["part"])
            if not ports:
                unknown.append(dict(instance=n["id"], issue="empty_connectivity"))
            for p in ports:
                if p.kind not in {"stud", "hole", "axle", "fixed", "hinge"}:
                    unknown.append(dict(instance=n["id"], issue="unsupported_connector", kind=p.kind))
                if p.kind in {"stud", "hole"} and (p.altitude in {0, -1} or p.area < 4):
                    continue
                result.append((n["id"], p, m[:3, 3] + m[:3, :3] @ p.position, m[:3, :3] @ p.direction, m[:3, :3] @ p.forward))
        except (ValueError, OSError) as e:
            unknown.append(dict(instance=n["id"], issue=str(e)))
    return result, unknown


def connections(nodes, catalog):
    ports, unknown = world_ports(nodes, catalog)
    edges = []
    def edge(a, b):
        edges.append(dict(a=a[0], b=b[0], port_a=a[1].id, port_b=b[1].id, kind=f"{a[1].kind}:{b[1].kind}", evidence="native-port-match.v1"))
    if ports:
        tree = cKDTree([p[2] for p in ports])
        for i, j in sorted(tree.query_pairs(.25)):
            a, b = ports[i], ports[j]
            if a[0] == b[0] or a[1].kind == b[1].kind == "axle" or not compatible(a[1], b[1]):
                continue
            dot = float(a[3] @ b[3])
            if abs(dot) >= .9999 and (a[1].kind == "hinge" or dot >= .9999):
                edge(a, b)
        shafts = [p for p in ports if p[1].kind == "axle"]
        if shafts:
            centres = np.array([p[2] - p[3] * p[1].length / 2 for p in shafts])
            radii = np.array([p[1].length / 2 for p in shafts])
            tree = cKDTree(centres)
            for i, a in enumerate(shafts):
                for j in tree.query_ball_point(centres[i], radii[i] + radii.max() + .25):
                    if j > i and a[0] != shafts[j][0] and shaft_match(a, shafts[j]):
                        edge(a, shafts[j])
    return edges, unknown


def shaft_diagnostics(nodes, catalog, components, limit=12, max_distance=80):
    """Bounded repair evidence for nearby shafts in disconnected components."""
    membership = {identity: index for index, members in enumerate(components) for identity in members}
    ports, _ = world_ports([node for node in nodes if node["kind"] == "part"], catalog)
    shafts = [p for p in ports if p[1].kind == "axle"]
    candidates = []
    if shafts:
        tree = cKDTree([p[2] for p in shafts])
        distances, neighbours = tree.query([p[2] for p in shafts], k=min(32, len(shafts)),
                                           distance_upper_bound=max_distance)
        distances = np.asarray(distances).reshape(len(shafts), -1)
        neighbours = np.asarray(neighbours).reshape(len(shafts), -1)
        for i, a in enumerate(shafts):
            for distance, j in zip(distances[i], neighbours[i]):
                if not np.isfinite(distance) or j <= i:
                    continue
                b = shafts[j]
                if membership.get(a[0]) == membership.get(b[0]) or not compatible(a[1], b[1]):
                    continue
                delta, dot = b[2] - a[2], float(a[3] @ b[3])
                offset = float(delta @ a[3])
                item = dict(a=a[0], b=b[0], port_a=a[1].id, port_b=b[1].id,
                    semantic_a=a[1].semantic, semantic_b=b[1].semantic,
                    origin_a_ldu=a[2].tolist(), origin_b_ldu=b[2].tolist(),
                    direction_a=a[3].tolist(), direction_b=b[3].tolist(),
                    endpoint_distance_ldu=float(distance),
                    lateral_error_ldu=float(np.linalg.norm(delta - offset * a[3])),
                    axis_angle_degrees=float(np.degrees(np.arccos(np.clip(abs(dot), 0, 1)))),
                    verified_contact=False, evidence="native-port-gap.v1")
                if abs(dot) >= .9999:
                    pa, pb = a[1], b[1]
                    la = pa.length - (2 if pa.semantic == "axle_guard" and pb.semantic != "pin_socket" else 0)
                    lb = pb.length - (2 if pb.semantic == "axle_guard" and pa.semantic != "pin_socket" else 0)
                    blo, bhi = sorted((offset, offset - np.sign(dot) * lb))
                    item.update(axial_gap_ldu=float(max(0, max(-la, blo) - min(0, bhi))),
                        engagement_ldu=float(max(0, min(0, bhi) - max(-la, blo))),
                        capped_a=[pa.start_capped, pa.end_capped], capped_b=[pb.start_capped, pb.end_capped])
                    if pa.cross_type and pb.cross_type:
                        item["key_angle_degrees"] = float(np.degrees(np.arccos(np.clip(abs(a[4] @ b[4]), 0, 1))))
                candidates.append(item)
    candidates.sort(key=lambda row: (row["lateral_error_ldu"], row["axis_angle_degrees"],
                                    row["endpoint_distance_ldu"], row["a"], row["b"], row["port_a"], row["port_b"]))
    by_port = {(p[0], p[1].id): p for p in shafts}
    by_id = {node["id"]: node for node in nodes}
    for item in candidates[:limit]:
        a, b = (by_port[(item[key], item["port_" + key])] for key in ("a", "b"))
        options = []
        for source, target in ((a, b), (b, a)):
            group = components[membership[source[0]]]
            if len(group) > 256:
                continue
            try:
                delta = shaft_snap_origin(source, target) - source[2]
            except ValueError:
                continue
            operations = []
            for identity in sorted(group):
                node = by_id[identity]
                world, local = np.asarray(node["world"]), np.array(node["local"], dtype=float)
                # Move the connected leaves, not an ancestor containing both endpoints.
                parent_rotation = world[:3, :3] @ local[:3, :3].T
                local[:3, 3] += parent_rotation.T @ delta
                operations.append(dict(op="transform", instance=identity, matrix=local.tolist()))
            options.append(dict(source=source[0], source_port=source[1].id,
                target=target[0], target_port=target[1].id, moving_parts=len(group),
                delta_ldu=delta.tolist(), delta_mm=(delta * .4).tolist(), operations=operations,
                status="proposal_requires_collision_and_motion_checks"))
        item["seating_options"] = sorted(options, key=lambda row: (row["moving_parts"], np.linalg.norm(row["delta_ldu"])))
    return dict(schema="shaft-repair-evidence.v1", status="diagnostic_only", exhaustive=False,
        search=dict(nearest_endpoints=32, max_distance_ldu=max_distance),
        candidates=candidates[:limit], candidate_count=len(candidates),
        guidance="Distances do not establish a legal connection. Check compatible insertion, caps, keyed orientation "
                 "and collisions. Verify joint ownership. Moving both connector endpoints in one subtree preserves "
                 "their relative gap; repair the correct independent subtree or local part transform.")


def intersects(a, b, tolerance=.35):
    ca, aa, ha = a
    cb, ab, hb = b
    # Conservative broadphase; retain the existing SAT tolerance for close pairs.
    if np.any(abs(cb-ca) > abs(aa) @ ha + abs(ab) @ hb + 1e-9):
        return False
    for axis in chain(aa.T, ab.T, (np.cross(x, y) for x in aa.T for y in ab.T)):
        length = np.linalg.norm(axis)
        if length < 1e-8:
            continue
        axis = axis / length
        if abs((cb - ca) @ axis) >= np.abs(aa.T @ axis) @ ha + np.abs(ab.T @ axis) @ hb - tolerance:
            return False
    return True


def colliders_intersect(boxes, other_boxes):
    """Vectorize only the existing broadphase; SAT still decides every close pair."""
    if not boxes or not other_boxes:
        return False
    centers = np.array([box[0] for box in boxes])
    other_centers = np.array([box[0] for box in other_boxes])
    extents = np.array([abs(axes) @ half for _, axes, half in boxes])
    other_extents = np.array([abs(axes) @ half for _, axes, half in other_boxes])
    for offset in range(0, len(boxes), 64):
        end = offset + 64
        separated = np.any(abs(centers[offset:end, None] - other_centers[None]) >
            extents[offset:end, None] + other_extents[None] + 1e-9, axis=2)
        for i, j in zip(*np.nonzero(~separated)):
            if intersects(boxes[offset + i], other_boxes[j]):
                return True
    return False


def validate(graph, catalog, collision_limit=100):
    nodes = [n for n in graph["instances"] if n["kind"] == "part"]
    edges, unknown = connections(nodes, catalog)
    connector_unknown = list(unknown)
    geometry_unknown = []
    geometry, collisions, invalid = {}, [], []
    surfaces = {}
    for n in nodes:
        m = np.asarray(n["world"])
        if not rigid(m, tolerance=.002):
            invalid.append(n["id"])
        try:
            if n["part"] not in surfaces:
                try:
                    triangles, _ = catalog.library.triangles(n["part"])
                    surfaces[n["part"]] = None if len(triangles) else "Part has no surface geometry"
                except (ValueError, OSError) as error:
                    surfaces[n["part"]] = str(error)
            if surfaces[n["part"]] is not None:
                raise ValueError(surfaces[n["part"]])
            boxes = [(m[:3, 3] + m[:3, :3] @ c, m[:3, :3] @ a, h) for c, a, h in catalog.boxes(n["part"])]
            lows = [c - abs(a) @ h for c, a, h in boxes]
            highs = [c + abs(a) @ h for c, a, h in boxes]
            geometry[n["id"]] = (boxes, np.min(lows, axis=0), np.max(highs, axis=0))
        except (ValueError, OSError) as e:
            issue = dict(instance=n["id"], issue=str(e))
            unknown.append(issue)
            geometry_unknown.append(issue)
    # Sweep along X before SAT so full vehicle checks do not compare every pair.
    active = []
    for identity, (boxes, low, high) in sorted(geometry.items(), key=lambda x: x[1][1][0]):
        active = [(i, g) for i, g in active if g[2][0] > low[0] + .35]
        for other, (other_boxes, lo, hi) in active:
            if np.any(np.minimum(high, hi) - np.maximum(low, lo) <= .35):
                continue
            if colliders_intersect(boxes, other_boxes):
                collisions.append(dict(a=other, b=identity, status="fail", evidence="native-collider-OBB"))
                if len(collisions) >= collision_limit:
                    break
        active.append((identity, (boxes, low, high)))
        if len(collisions) >= collision_limit:
            break
    adjacency = {n["id"]: set() for n in nodes}
    for e in edges:
        adjacency[e["a"]].add(e["b"])
        adjacency[e["b"]].add(e["a"])
    components, remaining = [], set(adjacency)
    while remaining:
        pending, component = [min(remaining)], set()
        while pending:
            p = pending.pop()
            if p not in component:
                component.add(p)
                pending.extend(adjacency[p] - component)
        remaining -= component
        components.append(sorted(component))
    connected = len(components) == 1 and bool(nodes)
    gates = dict(parts="unverified" if geometry_unknown or not nodes else "pass",
        transforms="fail" if invalid else "pass" if nodes else "unverified",
        connections="unverified" if connector_unknown or not nodes else "pass" if connected else "fail",
        collisions="fail" if collisions else "unverified" if geometry_unknown or not nodes else "pass")
    status = "fail" if "fail" in gates.values() else "unverified" if "unverified" in gates.values() else "pass"
    return dict(schema="assembly-check.v1", revision=graph["revision"], status=status, connections=edges,
                components=sorted(components, key=len, reverse=True), unknown=unknown, invalid_transforms=invalid,
                collisions=collisions, collision_limit_reached=len(collisions) >= collision_limit,
                coverage=dict(parts=len(nodes), geometry=len(geometry), connector_unknown=connector_unknown,
                              geometry_unknown=geometry_unknown),
                gates=dict(**gates, digital_assembly=status, appearance="unverified", studio="unverified", physical="unverified"))
