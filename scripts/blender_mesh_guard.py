"""Reject invalid procedural topology before Blender enters native edit-mode code."""
import math
import operator
import bpy


def validate_mesh_data(name, vertices, edges, faces):
    vertices, edges, faces = list(vertices), list(edges), list(faces)
    for index, point in enumerate(vertices):
        if len(point) != 3 or not all(math.isfinite(float(value)) for value in point):
            raise ValueError(f"Mesh {name}: vertex {index} requires three finite coordinates")
    for kind, rows, minimum in (("edge", edges, 2), ("face", faces, 3)):
        for number, row in enumerate(rows):
            if len(row) < minimum or (kind == "edge" and len(row) != 2):
                raise ValueError(f"Mesh {name}: {kind} {number} has invalid corner count")
            try:
                indices = [operator.index(value) for value in row]
            except TypeError as error:
                raise ValueError(f"Mesh {name}: {kind} {number} requires integer vertex indices") from error
            if any(index < 0 or index >= len(vertices) for index in indices):
                raise ValueError(f"Mesh {name}: {kind} {number} indices {indices} outside {len(vertices)} vertices")
            if len(set(indices)) != len(indices):
                raise ValueError(f"Mesh {name}: {kind} {number} repeats a vertex index")
    return vertices, edges, faces


_original_from_pydata = bpy.types.Mesh.from_pydata
if not getattr(_original_from_pydata, "_car2lego_mesh_guard", False):
    def checked_from_pydata(self, vertices, edges, faces, *args, _original=_original_from_pydata, **kwargs):
        vertices, edges, faces = validate_mesh_data(self.name, vertices, edges, faces)
        return _original(self, vertices, edges, faces, *args, **kwargs)

    checked_from_pydata._car2lego_mesh_guard = True
    bpy.types.Mesh.from_pydata = checked_from_pydata
