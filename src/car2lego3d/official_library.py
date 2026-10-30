"""Read-only delivery of the localized official-set community model collection."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata
from threading import RLock
from urllib.parse import urlencode
from zipfile import ZipFile

import numpy as np

from .common import ROOT, digest, write_json
from .ldraw_document import safe_name, parse_color
from .part_library import PartLibrary


def decode(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def identity_key(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def identity_pattern(value):
    key = identity_key(value)
    return re.compile(r"(?<![a-z0-9])" + r"[\W_]*".join(map(re.escape, key)) + r"(?![a-z0-9])") if key else None


@lru_cache(maxsize=1)
def known_brand_aliases():
    path = ROOT / "frontend/lib/data/vehicle-brands.json"
    if not path.is_file():
        return {}
    return {b["id"]: [b["id"], b["name"], b["name_zh"], *b.get("aliases", [])]
            for b in json.loads(path.read_text(encoding="utf-8"))["brands"]}


class ArchiveGeometry:
    """Preserve source transforms and resolve MPD, embedded and native geometry together."""

    def __init__(self, path, library):
        self.library = PartLibrary(library)
        self.sections, self.cache, self.missing, self.aliases = {}, {}, set(), {}
        self.root = "__root__"
        with ZipFile(path) as archive:
            archive.setpassword(b"soho0909")
            text = decode(archive.read("model.ldr"))
            current = "__root__"
            self.sections[current] = []
            found_root = False
            for line in text.splitlines():
                line = line.strip().lstrip("\ufeff")
                if line.upper().startswith("0 FILE "):
                    current = safe_name(line[7:])
                    if current in self.sections:
                        raise ValueError("Duplicate model section")
                    self.sections[current] = []
                    if not found_root:
                        self.root, found_root = current, True
                elif line.upper() == "0 NOFILE":
                    current = "__root__"
                else:
                    self.sections[current].append(line)
            for member in archive.namelist():
                normal = member.replace("\\", "/")
                if not normal.lower().startswith("customparts/") or not normal.lower().endswith(".dat"):
                    continue
                name = safe_name(normal.split("/", 1)[1])
                lines = decode(archive.read(member)).splitlines()
                self.sections.setdefault(name, lines)
                for prefix in ("parts/", "p/"):
                    if name.startswith(prefix):
                        self.sections.setdefault(name[len(prefix):], lines)

    def lines(self, name):
        if name in self.sections:
            return self.sections[name]
        try:
            path = self.library.resolve(name)
            if ("primitive", name) in self.library.geometry_warnings:
                self.aliases[name] = "48/" + name
            return decode(path.read_bytes()).splitlines()
        except FileNotFoundError:
            if "/" not in name:
                try:
                    path = self.library.resolve("48/" + name)
                    self.aliases[name] = "48/" + name
                    return decode(path.read_bytes()).splitlines()
                except FileNotFoundError:
                    pass
            self.missing.add(name)
            return []

    @staticmethod
    def reference(line):
        fields = line.split(maxsplit=14)
        if len(fields) != 15:
            raise ValueError("Malformed LDraw reference")
        values = np.asarray([float(x) for x in fields[2:14]])
        if not np.isfinite(values).all():
            raise ValueError("Non-finite LDraw reference")
        matrix = np.eye(4)
        matrix[:3, 3], matrix[:3, :3] = values[:3], values[3:].reshape(3, 3)
        return safe_name(fields[14]), parse_color(fields[1]), matrix

    def instances(self):
        instances = []

        def visit(name, matrix, inherited, stack):
            if name in stack or len(stack) > 64:
                raise ValueError("Cyclic or excessively nested model")
            # LSynth and older MPDs may put flexible surfaces directly into LDR sections.
            if any(line.split(maxsplit=1)[:1] in (["3"], ["4"]) for line in self.sections[name]):
                instances.append((name, inherited, matrix))
                return
            for line in self.sections[name]:
                if line.split(maxsplit=1)[:1] != ["1"]:
                    continue
                child, color, transform = self.reference(line)
                color = inherited if color == 16 else color
                transform = matrix @ transform
                if child in self.sections and not child.endswith(".dat"):
                    visit(child, transform, color, (*stack, name))
                else:
                    instances.append((child, color, transform))
                    if len(instances) > 200000:
                        raise ValueError("Model exceeds preview instance limit")
        visit(self.root, np.eye(4), 7, ())
        return instances

    def triangles(self, name, stack=()):
        name = safe_name(name)
        if name in stack or len(stack) > 64:
            raise ValueError("Cyclic or excessively nested part")
        if name in self.cache:
            return self.cache[name]
        vertices, colors = [], []
        for line in self.lines(name):
            fields = line.split()
            if not fields or fields[0] in {"0", "2", "5"}:
                continue
            if fields[0] == "1":
                child, color, matrix = self.reference(line)
                geometry, child_colors = self.triangles(child, (*stack, name))
                vertices.extend(geometry @ matrix[:3, :3].T + matrix[:3, 3])
                colors.extend(np.where(child_colors == 16, color, child_colors))
            elif fields[0] in {"3", "4"}:
                points = np.asarray([float(v) for v in fields[2:]]).reshape(-1, 3)
                if not np.isfinite(points).all():
                    raise ValueError("Non-finite part geometry")
                for indices in ((0, 1, 2),) if fields[0] == "3" else ((0, 1, 2), (0, 2, 3)):
                    vertices.append(points[list(indices)])
                    colors.append(parse_color(fields[1]))
        result = np.asarray(vertices, dtype=np.float32).reshape(-1, 3, 3), np.asarray(colors, dtype=np.int64)
        self.cache[name] = result
        return result

    def export(self, destination):
        import trimesh
        from trimesh.visual.material import PBRMaterial
        palette = {}
        config = self.library.root / "LDConfig.ldr"
        if config.is_file():
            for line in decode(config.read_bytes()).splitlines():
                match = re.search(r"!COLOUR\s+(.*?)\s+CODE\s+(\d+)\s+VALUE\s+(#[A-Fa-f\d]{6})\s+EDGE\s+\S+(.*)", line)
                if match:
                    rgba = [int(match[3][i:i+2], 16) for i in (1, 3, 5)] + [255]
                    alpha = re.search(r"ALPHA\s+(\d+)", match[4])
                    if alpha:
                        rgba[3] = int(alpha[1])
                    palette[int(match[2])] = rgba
        scene, reusable, count = trimesh.Scene(), {}, 0
        # LDraw's downwards Y axis is rotated as a complete frame.
        frame = np.diag([1., -1., -1., 1.])
        for name, instance_color, matrix in self.instances():
            vertices, colors = self.triangles(name)
            for color in np.unique(colors):
                paint = instance_color if color == 16 else int(color)
                key = (name, int(color), paint)
                if key not in reusable:
                    triangles = vertices[colors == color]
                    if not len(triangles):
                        continue
                    rgba = ([((paint & 0xffffff) >> shift) & 255 for shift in (16, 8, 0)] + [128 if paint >> 24 == 3 else 255]
                            if paint >= 0x2000000 else palette.get(paint, [185, 185, 185, 255]))
                    material = PBRMaterial(baseColorFactor=rgba, metallicFactor=0.03, roughnessFactor=.3,
                                           alphaMode="BLEND" if rgba[3] < 255 else "OPAQUE", doubleSided=True)
                    mesh = trimesh.Trimesh(vertices=triangles.reshape(-1, 3),
                                           faces=np.arange(len(triangles)*3).reshape(-1, 3), process=False)
                    mesh.visual = trimesh.visual.TextureVisuals(material=material)
                    geom_name = f"g{len(reusable)}"
                    scene.geometry[geom_name] = mesh
                    reusable[key] = geom_name
                scene.graph.update(frame_to=f"p{count}", matrix=frame @ matrix, geometry=reusable[key])
                count += 1
        if not count:
            raise ValueError("No renderable geometry in model")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        def linear_colors(tree):
            # LDraw values are sRGB; glTF baseColorFactor is linear RGB.
            for material in tree.get("materials", []):
                factor = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
                if factor:
                    factor[:3] = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                                  for value in factor[:3]]
        temporary.write_bytes(scene.export(file_type="glb", tree_postprocessor=linear_colors))
        temporary.replace(destination)
        return dict(mesh_instances=count, missing_geometry=sorted(self.missing), primitive_aliases=self.aliases,
                    source_instance_count=len(self.instances()), geometry_definitions=len(reusable),
                    geometry_dependencies=len(self.library.used), bytes=destination.stat().st_size)


class OfficialLibrary:
    def __init__(self, catalog=None, root=None, cache=None, parts=None):
        self.catalog_path = Path(catalog or ROOT / "data/official-library/catalog.zh.json")
        self.root = Path(root or os.environ.get("CAR2LEGO_MODEL_LIBRARY", r"D:\lego\result")).resolve()
        self.cache = Path(cache or ROOT / "runs/official-library-previews")
        self.parts = Path(parts or os.environ.get("LDRAW_LIBRARY_PATH", r"D:\lego\Studio 2.0\ldraw"))
        self.lock, self.pool, self.jobs = RLock(), ThreadPoolExecutor(max_workers=1), {}
        self._stamp, self._data = None, None

    def data(self):
        if not self.catalog_path.is_file():
            return dict(models=[], types={})
        stamp = self.catalog_path.stat().st_mtime_ns
        with self.lock:
            if self._stamp != stamp:
                self._data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                self._stamp = stamp
            return self._data

    def record(self, id):
        if not re.fullmatch(r"[a-f0-9]{24}", id):
            raise FileNotFoundError("车型不存在。")
        row = next((r for r in self.data()["models"] if r["id"] == id), None)
        if row is None:
            raise FileNotFoundError("车型不存在。")
        return row

    def file(self, id, thumbnail=False):
        row = self.record(id)
        name = row.get("thumbnail_file") if thumbnail else row["file"]
        if not name:
            raise FileNotFoundError("文件不存在。")
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise FileNotFoundError("模型文件不存在。")
        return path

    @staticmethod
    def public(row):
        data = {k: v for k, v in row.items() if k not in {"file", "thumbnail_file", "original_file"}}
        base = "/api/v1/sets/library/" + row["id"]
        data.update(download_url=base+"/download", filename=row["file"],
                    thumbnail_url=base+"/thumbnail" if row.get("thumbnail_file") else row.get("image_url"))
        return data

    def vehicle_matches(self, make, model, year=None):
        if not isinstance(make, str) or not isinstance(model, str) or len(make) > 100 or len(model) > 100:
            raise ValueError("无效的品牌或车型。")
        if year not in (None, "") and (isinstance(year, bool) or not str(year).isdigit() or not 1900 <= int(year) <= 2030):
            raise ValueError("年款应为 1900 至 2030，或留空。")
        brand_key, model = identity_key(make), unicodedata.normalize("NFKC", model).casefold().strip()
        if not brand_key or not model:
            return []
        brands = {b["id"]: b for r in self.data()["models"] for b in r["brands"]}
        aliases = {id: [id, b["en"], b["zh"], *known_brand_aliases().get(id, [])] for id, b in brands.items()}
        ids = {id for id, names in aliases.items() if brand_key in {identity_key(n) for n in names}}
        if not ids:
            return []
        # A pasted full vehicle name may repeat the selected brand.
        for name in sorted({n for id in ids for n in aliases[id]}, key=lambda n: len(identity_key(n)), reverse=True):
            pattern = identity_pattern(name)
            prefix = pattern.match(model) if pattern else None
            if prefix:
                model = model[prefix.end():].strip(" -_·")
                break
        pattern = identity_pattern(model)
        if pattern is None:
            return []
        return [row for row in self.data()["models"]
                if any(b["id"] in ids for b in row["brands"])
                and (not year or row.get("vehicle_year") is None or row["vehicle_year"] == int(year))
                and any(pattern.search(unicodedata.normalize("NFKC", row.get(key) or "").casefold())
                        for key in ("name_zh", "original_name", "model"))]

    def match_vehicle(self, make, model, year=None):
        rows = self.vehicle_matches(make, model, year)
        if not rows:
            return None
        params = dict(brand=rows[0]["brands"][0]["id"], match_make=make.strip(), match_model=model.strip())
        # Multi-brand sets need the brand which was requested, rather than the first brand.
        names = known_brand_aliases()
        for brand in rows[0]["brands"]:
            if identity_key(make) in {identity_key(n) for n in [brand["id"], brand["en"], brand["zh"], *names.get(brand["id"], [])]}:
                params["brand"] = brand["id"]
                break
        if year:
            params["match_year"] = str(year)
        url = ("/sets/" + rows[0]["id"] if len(rows) == 1 else "/sets") + "?" + urlencode(params)
        return dict(total=len(rows), url=url, model_ids=[row["id"] for row in rows])

    def listing(self, params):
        models = self.data()["models"]
        if params.get("match_model"):
            models = self.vehicle_matches(params.get("match_make", [""])[0], params["match_model"][0],
                                          params.get("match_year", [None])[0])
        query, brand, body, year = (params.get(k, [""])[0] for k in ("q", "brand", "type", "year"))
        query = query.strip().casefold()[:200]
        try:
            page, per_page = max(1, int(params.get("page", [1])[0])), min(60, max(1, int(params.get("limit", [24])[0])))
        except (ValueError, TypeError):
            raise ValueError("无效分页参数。")
        if year and not re.fullmatch(r"(?:vehicle|release):(?:\d{4}|unknown)", year):
            raise ValueError("无效年份。")
        def matches(row):
            if brand and not any(b["id"] == brand for b in row["brands"]):
                return False
            if body and body not in row["vehicle_types"]:
                return False
            if year:
                kind, value = year.split(":")
                actual = row["vehicle_year" if kind == "vehicle" else "release_year"]
                if str(actual) != value and not (value == "unknown" and actual is None):
                    return False
            search = " ".join(str(row.get(k) or "") for k in ("name_zh", "original_name", "set_num", "release_year", "vehicle_year", "variant_zh"))
            search += " " + " ".join(b["en"] + " " + b["zh"] for b in row["brands"]) + " " + " ".join(row["vehicle_type_labels"])
            return all(word in search.casefold() for word in query.split())
        filtered = [r for r in models if matches(r)]
        pages = max(1, math.ceil(len(filtered) / per_page))
        page = min(page, pages)
        collection = self.data()["models"]
        brands = {b["id"]: b for r in collection for b in r["brands"]}
        types = {t for r in collection for t in r["vehicle_types"]}
        return dict(models=[self.public(r) for r in filtered[(page-1)*per_page:page*per_page]],
                    total=len(filtered), collection_total=len(collection), page=page, pages=pages, per_page=per_page,
                    facets=dict(brands=sorted(brands.values(), key=lambda b: (b["en"] == "LEGO", b["zh"])),
                                types=[dict(id=t, label=label) for t, label in self.data()["types"].items() if t in types],
                                vehicle_years=sorted({r["vehicle_year"] for r in collection if r.get("vehicle_year")}, reverse=True),
                                release_years=sorted({r["release_year"] for r in collection if r.get("release_year")}, reverse=True)))

    def preview_paths(self, id):
        row = self.record(id)
        config = self.parts / "LDConfig.ldr"
        version = "library-glb-v4|" + row["sha256"] + "|" + str(self.parts) + "|" + (digest(config) if config.is_file() else "missing")
        folder = self.cache / hashlib.sha256(version.encode()).hexdigest()[:32]
        return folder / "model.glb", folder / "status.json"

    def preview(self, id, retry=False):
        target, report = self.preview_paths(id)
        with self.lock:
            if report.is_file():
                state = json.loads(report.read_text(encoding="utf-8"))
                if state["status"] == "ready" and target.is_file():
                    return {**state, "model_url": f"/api/v1/sets/library/{id}/model.glb"}
                if state["status"] == "failed" and not retry:
                    return state
            job = self.jobs.get(id)
            if job and not job.done():
                return dict(status="processing")
            self.jobs[id] = self.pool.submit(self._build, id, target, report)
            return dict(status="processing")

    def _build(self, id, target, report):
        try:
            source = self.file(id)
            if digest(source) != self.record(id)["sha256"]:
                raise ValueError("模型文件校验不一致，请重新同步。")
            geometry = ArchiveGeometry(source, self.parts)
            result = geometry.export(target)
            write_json(report, dict(status="ready", **result))
        except Exception as error:
            write_json(report, dict(status="failed", error="无法生成此模型的 3D 预览。", diagnostic=type(error).__name__+": "+str(error)))


@lru_cache(maxsize=1)
def official_library():
    return OfficialLibrary()
