"""One recursive geometry resolver for native and packaged LDraw parts."""
from pathlib import Path
import json

import numpy as np

from .common import digest
from .ldraw_document import Document, safe_name, parse_color


class PartLibrary:
    def __init__(self, root, packages=()):
        self.root = Path(root).resolve()
        self.packages = tuple(Path(p).resolve() for p in packages)
        self.used = {}
        self._cache = {}
        self.ignored_edge_records = {}
        self.geometry_warnings = {}

    def identity_alias(self, name):
        lines = self.resolve(name).read_text(encoding='utf-8-sig').splitlines()
        if not any(line.split()[:4] == ['0', '!LDRAW_ORG', 'Part', 'Alias'] for line in lines):
            return None
        geometry = [line for line in lines if line.split() and line.split()[0] != '0']
        if len(geometry) != 1 or geometry[0].split()[0] != '1':
            return None
        reference = Document.parse_line(geometry[0], geometry=True)
        if reference.color != 16 or not np.array_equal(reference.matrix(), np.eye(4)):
            return None
        return reference.name

    def resolve(self, name):
        name = safe_name(name)
        roots = [*self.packages, self.root, self.root / 'UnOfficial']
        for root in roots:
            for folder in ('parts', 'p', '') if root in self.packages else ('parts', 'p'):
                path = (root / folder / name).resolve()
                if path.is_relative_to(root) and path.is_file():
                    self.used[str(path)] = digest(path)
                    return path
        if '/' not in name:
            for root in roots:
                path = (root / 'p/48' / name).resolve()
                if path.is_relative_to(root) and path.is_file():
                    self.used[str(path)] = digest(path)
                    self.geometry_warnings[('primitive', name)] = dict(file=str(path), sha256=digest(path),
                        requested=name, issue='Unqualified missing primitive resolved from p/48')
                    return path
        elif name.startswith('48/') and name.count('/') == 1:
            # Some Studio bundles flatten high-resolution primitives into p/.
            # Require their declared logical name and resolution, never a low-res substitute.
            for root in roots:
                path = (root / 'p' / name.split('/')[1]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    continue
                records = [line.split() for line in path.read_text(encoding='utf-8-sig').splitlines()
                           if line.startswith(('0 Name:', '0 !LDRAW_ORG'))]
                declared = [safe_name(' '.join(row[2:])) for row in records if row[:2] == ['0', 'Name:']]
                high_resolution = any(row[:2] == ['0', '!LDRAW_ORG'] and len(row) >= 3
                    and row[2] in {'48_Primitive', 'Unofficial_48_Primitive'} for row in records)
                if declared == [name] and high_resolution:
                    self.used[str(path)] = digest(path)
                    self.geometry_warnings[('primitive', name)] = dict(file=str(path), sha256=digest(path),
                        requested=name, issue='Declared high-resolution primitive stored in p root')
                    return path
        raise FileNotFoundError('Missing LDraw geometry: ' + name)

    def triangles(self, name, stack=()):
        name = safe_name(name)
        if name in stack:
            raise ValueError('Cyclic LDraw part dependency: ' + name)
        cached = self._cache.get(name)
        if cached and all(Path(path).is_file() and digest(path) == sha for path, sha in cached[2].items()):
            self.used.update(cached[2])
            self.ignored_edge_records.update(cached[3])
            self.geometry_warnings.update(cached[4])
            return cached[:2]
        path = self.resolve(name)
        dependencies = {str(path): digest(path)}
        triangles, colors, ignored, warnings = [], [], {}, {}
        if ('primitive', name) in self.geometry_warnings:
            warnings[('primitive', name)] = self.geometry_warnings[('primitive', name)]
        for number, line in enumerate(path.read_text(encoding='utf-8-sig', errors='strict').splitlines(), 1):
            tokens = line.split()
            if not tokens or tokens[0] == '0':
                continue
            if tokens[0] in {'2', '5'}:
                try:
                    Document.parse_line(line, geometry=True)
                except ValueError:
                    ignored[(str(path), number)] = dict(file=str(path), sha256=dependencies[str(path)], line=number, record=line,
                        issue='Malformed non-surface edge ignored during triangle extraction')
                continue
            surface = line
            # Studio appends one (u,v) pair per vertex to textured faces.
            if tokens[0] in {'3', '4'} and len(tokens) == {'3': 17, '4': 22}[tokens[0]]:
                if not np.isfinite(np.asarray(tokens[2:], dtype=float)).all():
                    raise ValueError('Malformed textured surface geometry')
                surface = ' '.join(tokens[:{'3': 11, '4': 14}[tokens[0]]])
                warnings[(str(path), number)] = dict(file=str(path), sha256=dependencies[str(path)], line=number,
                    issue='Studio UV coordinates retained in source; rendered texture appearance unverified')
            ref = Document.parse_line(surface, geometry=True)
            if tokens[0] == '1':
                child, color = self.triangles(ref.name, (*stack, name))
                dependencies.update(self._cache[ref.name][2])
                ignored.update(self._cache[ref.name][3])
                warnings.update(self._cache[ref.name][4])
                transform = ref.matrix()
                triangles.extend(child @ transform[:3, :3].T + transform[:3, 3])
                colors.extend(np.where(color == 16, ref.color, color).tolist())
            elif tokens[0] in {'3', '4'}:
                vertices = np.asarray(surface.split()[2:], dtype=float).reshape(-1, 3)
                for indices in ((0, 1, 2),) if tokens[0] == '3' else ((0, 1, 2), (0, 2, 3)):
                    triangles.append(vertices[list(indices)])
                    colors.append(parse_color(tokens[1]))
            else:
                raise ValueError('Unexpected LDraw record in ' + name)
        result = np.asarray(triangles, dtype=float).reshape(-1, 3, 3), np.asarray(colors, dtype=int)
        self._cache[name] = (*result, dependencies, ignored, warnings)
        self.used.update(dependencies)
        self.ignored_edge_records.update(ignored)
        self.geometry_warnings.update(warnings)
        return result

    def instance(self, row):
        triangles, colors = self.triangles(row['part'] if row['part'].endswith('.dat') else row['part'] + '.dat')
        rotation = np.asarray(row['rotation']).reshape(3, 3)
        position = np.asarray(row['position'])
        if (not np.isfinite(rotation).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
                or position.shape != (3,) or not np.isfinite(position).all()):
            raise ValueError('An assembly instance stretches a native part')
        return triangles @ rotation.T + position, np.where(colors == 16, row['color'], colors)

    def geometry(self, rows):
        result = {}
        for row in rows:
            name = row['part'].removesuffix('.dat') + '.dat'
            if name in result:
                continue
            triangles, colors = self.triangles(name)
            # Existing desktop viewer consumes native LDU positions and face color groups.
            result[name] = [dict(color_id=int(color), positions=triangles[colors == color].reshape(-1).tolist())
                            for color in np.unique(colors)]
        return result

    def dependency_record(self):
        return dict(schema='car2lego3d.part-dependencies.v1', files=[
            dict(file=path, sha256=sha) for path, sha in sorted(self.used.items())],
            missing_geometry=[], ignored_edge_records=list(self.ignored_edge_records.values()),
            geometry_warnings=list(self.geometry_warnings.values()), physical_verified=False)


def assembly_library(assembly_path, root):
    from .part_package import validate_package
    assembly_path = Path(assembly_path).resolve()
    assembly = json.loads(assembly_path.read_text(encoding='utf-8'))
    packages = []
    for relative in assembly.get('part_packages', []):
        package = (assembly_path.parent / relative).resolve()
        if not package.is_relative_to(assembly_path.parent):
            raise ValueError('Part package escaped assembly directory')
        validate_package(package)
        packages.append(package)
    return PartLibrary(root, packages)
