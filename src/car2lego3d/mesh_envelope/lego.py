import csv
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree

from ..common import digest, stage, write_json

# LDraw long plates extend along X, regardless of their short-by-long display name.
# Footprints are X/Z stud counts, verified against native .dat files.
PARTS = {"3024": (1, 1), "3023": (2, 1), "3623": (3, 1), "3710": (4, 1),
         "3666": (6, 1), "3460": (8, 1), "3022": (2, 2), "3021": (3, 2),
         "3020": (4, 2), "3795": (6, 2), "3034": (8, 2)}
PALETTE = {0: (5, 19, 29), 1: (0, 85, 191), 2: (37, 122, 62), 4: (201, 26, 9),
           14: (242, 205, 55), 15: (242, 243, 242), 19: (228, 205, 158),
           25: (254, 138, 24), 70: (88, 42, 18), 71: (160, 165, 169),
           72: (108, 110, 104), 272: (13, 50, 91)}


@dataclass(frozen=True)
class Plate:
    part: str
    color: int
    x: int
    y: int
    z: int
    width: int
    depth: int
    rotated: bool = False

    def cells(self):
        return ((self.x + dx, self.y + dy, self.z)
                for dx in range(self.width) for dy in range(self.depth))


def load_mesh(source):
    mesh = trimesh.load(source, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError("Input must contain a triangle mesh.")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError("Mesh contains non-finite vertices.")
    return mesh


def color_visual(mesh):
    return mesh.visual.to_color() if mesh.visual.kind == "texture" else mesh.visual


def orient(mesh, up_axis="z", yaw=0):
    mesh = mesh.copy()
    # Convert up to Z, then align the principal horizontal direction to the stud grid.
    if up_axis == "y":
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    elif up_axis == "x":
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0]))
    _, vectors = np.linalg.eigh(np.cov(mesh.vertices[:, :2].T))
    direction = vectors[:, -1]
    angle = float(-np.arctan2(direction[1], direction[0]))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(angle + np.deg2rad(yaw), [0, 0, 1]))
    return mesh, float(np.rad2deg(angle))


def voxelize(mesh, length_studs=32, up_axis="z", yaw=0, color=None):
    if not 4 <= length_studs <= 128:
        raise ValueError("length_studs must be between 4 and 128.")
    mesh, alignment = orient(mesh, up_axis, yaw)
    if mesh.extents[0] < 1e-8 or mesh.extents[2] < 1e-8:
        raise ValueError("Mesh is degenerate; a volumetric object is required.")
    scale_mm = (length_studs - 1) * 8 / mesh.extents[0]
    mesh.vertices = (mesh.vertices - mesh.bounds[0]) * scale_mm / np.array([8, 8, 3.2])
    if np.prod(np.ceil(mesh.extents + 3)) > 8_000_000:
        raise ValueError("Voxel grid too large. Reduce --length-studs or correct --up-axis.")
    grid = mesh.voxelized(pitch=1.0).fill()
    matrix = grid.matrix.copy()
    labels, count = ndimage.label(matrix)
    sizes = np.bincount(labels.ravel()); sizes[0] = 0
    retained = labels == sizes.argmax()
    removed = int(matrix.sum() - retained.sum())
    points = np.rint(grid.indices_to_points(np.argwhere(retained))).astype(int)
    if len(points) == 0:
        raise ValueError("Voxelization produced no occupied cells.")
    origin = points.min(axis=0)
    coords = points - origin
    if color is not None:
        if color not in PALETTE:
            raise ValueError(f"Unsupported palette color: {color}")
        colors = np.full(len(points), color, dtype=int)
    else:
        nearest = cKDTree(mesh.vertices).query(points)[1]
        rgb = color_visual(mesh).vertex_colors[nearest, :3].astype(float)
        ids, palette = np.array(list(PALETTE)), np.array(list(PALETTE.values()))
        colors = ids[((rgb[:, None] - palette[None]) ** 2).sum(axis=-1).argmin(axis=1)]
    cells = {tuple(map(int, point)): int(value) for point, value in zip(coords, colors)}
    return cells, {"grid_shape": (coords.max(axis=0) + 1).tolist(),
                   "source_mesh_components": len(mesh.split(only_watertight=False)),
                   "voxel_components_before_filter": count, "discarded_voxels": removed,
                   "scale_mm_per_source_unit": float(scale_mm), "up_axis": up_axis, "yaw_degrees": yaw,
                   "principal_axis_alignment_degrees": alignment,
                   "cell_mm": [8, 8, 3.2], "fill": "trimesh solid fill; largest face-connected voxel component"}


def pack(cells):
    remaining = dict(cells)
    owners = {}
    plates = []
    candidates = [(part, w, d, rotate) for part, (w0, d0) in PARTS.items()
                  for w, d, rotate in ([(w0, d0, False)] if w0 == d0 else [(w0, d0, False), (d0, w0, True)])]
    order = sorted(cells, key=lambda p: (p[2], p[1] if p[2] % 2 == 0 else p[0], p[0] if p[2] % 2 == 0 else p[1]))
    for x, y, z in order:
        if (x, y, z) not in remaining:
            continue
        color = remaining[(x, y, z)]
        choices = []
        for part, w, d, rotate in candidates:
            footprint = [(x + dx, y + dy, z) for dx in range(w) for dy in range(d)]
            if not all(remaining.get(p) == color for p in footprint):
                continue
            below = {owners[(px, py, pz - 1)] for px, py, pz in footprint if (px, py, pz - 1) in owners}
            choices.append((w * d + 0.2 * len(below), w if z % 2 else d, part, w, d, rotate, footprint))
        _, _, part, w, d, rotate, footprint = max(choices)
        index = len(plates)
        plates.append(Plate(part, color, x, y, z, w, d, rotate))
        for point in footprint:
            del remaining[point]
            owners[point] = index
    return plates


def validate(plates, target):
    owners, overlaps = {}, []
    for index, plate in enumerate(plates):
        expected = PARTS.get(plate.part)
        actual = (plate.depth, plate.width) if plate.rotated else (plate.width, plate.depth)
        if expected != actual or plate.color not in PALETTE:
            raise ValueError("Invalid part footprint or color.")
        for point in plate.cells():
            if point in owners:
                overlaps.append(point)
            owners[point] = index
    edges = set()
    supported = set()
    for (x, y, z), upper in owners.items():
        lower = owners.get((x, y, z - 1))
        if lower is not None:
            edges.add((min(lower, upper), max(lower, upper)))
            supported.add(upper)
    parents = list(range(len(plates)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for a, b in edges:
        parents[root(a)] = root(b)
    components = Counter(root(i) for i in range(len(plates)))
    missing = set(target) - set(owners)
    extra = set(owners) - set(target)
    return {"part_count": len(plates), "occupied_voxels": len(owners), "overlapping_voxels": len(overlaps),
            "missing_voxels": len(missing), "extra_voxels": len(extra),
            "coverage_exact": not (overlaps or missing or extra),
            "stud_connected_components": len(components), "component_sizes": sorted(components.values(), reverse=True),
            "stud_connections": len(edges),
            "unsupported_above_ground": [i for i, plate in enumerate(plates) if plate.z > 0 and i not in supported],
            "buildability": "unverified", "studio_verified": False, "physical_verified": False,
            "limitations": ["Stud graph assumes full rectangular plate footprints and vertical clutch connections.",
                           "No clutch force, center-of-mass, insertion, native collider, wheel or mechanism verification.",
                           "GLB previews approximate parts with cuboids; use LDraw in Studio for real part geometry."]}


def export(plates, directory, dimensions):
    directory = Path(directory)
    center_x, center_y = (dimensions[0] - 1) / 2, (dimensions[1] - 1) / 2
    lines = ["0 Car2LEGO_3D mesh-derived plate sculpture", "0 Author: imxiaoxian",
             "0 // Research draft. Studio and physical assembly unverified."]
    for z in sorted({p.z for p in plates}):
        lines.append(f"0 // Plate layer {z + 1}")
        for p in plates:
            if p.z != z:
                continue
            x = (p.x + (p.width - 1) / 2 - center_x) * 20
            y = -(p.z + 1) * 8
            depth = (p.y + (p.depth - 1) / 2 - center_y) * 20
            rotation = "0 0 1 0 1 0 -1 0 0" if p.rotated else "1 0 0 0 1 0 0 0 1"
            lines.append(f"1 {p.color} {x:g} {y:g} {depth:g} {rotation} {p.part}.dat")
        lines.append("0 STEP")
    (directory / "model.ldr").write_text("\n".join(lines) + "\n", encoding="ascii")
    counts = Counter((p.part, p.color) for p in plates)
    with (directory / "inventory.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["part", "ldraw_color", "quantity"])
        writer.writerows((part, color, count) for (part, color), count in sorted(counts.items()))
    # Rebrickable/BrickLink color IDs differ from LDraw, so CSV is authoritative.
    write_json(directory / "assembly.json", {"units": "stud_x,stud_y,plate_z", "plates": [asdict(p) for p in plates]})
    shapes = []
    for p in plates:
        box = trimesh.creation.box(extents=[p.width * 8 - 0.1, p.depth * 8 - 0.1, 3.2])
        box.apply_translation([(p.x + (p.width - 1) / 2) * 8,
                               (p.y + (p.depth - 1) / 2) * 8, (p.z + 0.5) * 3.2])
        box.visual.vertex_colors = [*PALETTE[p.color], 255]
        shapes.append(box)
    trimesh.util.concatenate(shapes).export(directory / "lego.glb")


def convert(source, directory, length_studs=32, up_axis="z", yaw=0, color=None):
    source, directory = Path(source), Path(directory)
    with stage(directory, "lego", {"mesh": source.name, "sha256": digest(source), "length_studs": length_studs,
                                   "up_axis": up_axis, "yaw": yaw, "color": color}) as record:
        mesh = load_mesh(source)
        cells, info = voxelize(mesh, length_studs, up_axis, yaw, color)
        plates = pack(cells)
        report = validate(plates, cells)
        export(plates, directory, info["grid_shape"])
        write_json(directory / "validation.json", {**info, **report})
        record.update(**report, dimensions_mm=[info["grid_shape"][i] * size for i, size in enumerate((8, 8, 3.2))],
                      outputs={name: digest(directory / name) for name in ("model.ldr", "lego.glb", "inventory.csv")})
        from ..preview import render
        render(orient(mesh, up_axis, yaw)[0], directory / "mesh-preview.png")
        render(load_mesh(directory / "lego.glb"), directory / "lego-preview.png")
    return directory / "model.ldr"
