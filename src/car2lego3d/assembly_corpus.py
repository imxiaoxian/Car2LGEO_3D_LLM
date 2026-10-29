"""Read-only corpus inventory and content-addressed module retrieval."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile

import numpy as np

from .assembly_graph import Assembly, PASSWORD, reference, reference_line
from .common import digest, write_json
from .ldraw_document import safe_name

SCHEMA = "assembly-corpus.v1"


def dependency_hashes(manifest):
    """Hash geometry and sidecars without losing namespaces or file extensions."""
    grouped = {}
    for entry in manifest:
        name = entry["path"].replace("\\", "/").casefold()
        for prefix in ("parts/", "p/"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        key = str(Path(name).with_suffix(".dat")).replace("\\", "/")
        grouped.setdefault(key, []).append([name, entry["sha256"]])
    return {name: sha256(json.dumps(sorted(items), separators=(",", ":")).encode()).hexdigest()
            for name, items in grouped.items()}


def canonical_module(assembly, section, stack=(), dependencies=None):
    if section in stack:
        raise ValueError("Cyclic module")
    result = []
    for row in assembly.sections[section]:
        ref = reference(row["line"])
        if ref:
            name = ref["name"]
            child = canonical_module(assembly, name, (*stack, section), dependencies) if name in assembly.sections else [name, (dependencies or {}).get(name)]
            result.append([ref["color"], np.round(ref["matrix"], 7).tolist(), child])
        elif row["line"].strip() == "0 STEP":
            result.append("step")
        elif row["line"].split() and row["line"].split()[0] in {"2", "3", "4", "5"}:
            result.append(row["line"].split())
    return sha256(json.dumps(result, separators=(",", ":")).encode()).hexdigest()


def extract_module(assembly, section):
    sections, pending = {}, [section]
    while pending:
        name = pending.pop()
        if name in sections:
            continue
        sections[name] = assembly.sections[name]
        for row in sections[name]:
            ref = reference(row["line"])
            if ref and ref["name"] in assembly.sections:
                pending.append(ref["name"])
    return "".join("0 FILE " + name + "\n" + "\n".join(r["line"] for r in rows) + "\n0 NOFILE\n"
                   for name, rows in sections.items())


def embedded_packages(path, output):
    """Extract only LDraw dependencies into this project's cache, never the corpus."""
    output = Path(output)
    manifest = []
    with ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name.lower().startswith("customparts/"):
                continue
            relative = safe_name(name.split("/", 1)[1])
            if not relative.lower().endswith((".dat", ".ldr", ".conn", ".col")):
                continue
            if info.file_size > 64 * 1024 * 1024:
                raise ValueError("Embedded dependency exceeds size limit")
            data = archive.read(info, pwd=PASSWORD)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or sha256(destination.read_bytes()).digest() != sha256(data).digest():
                destination.write_bytes(data)
            manifest.append(dict(path=relative, sha256=sha256(data).hexdigest(), bytes=len(data)))
    return manifest


class Corpus:
    def __init__(self, directory, *, snapshot=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.frozen_snapshot = snapshot
        if snapshot:
            if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise ValueError("Invalid corpus snapshot")
            folder = self.directory / "snapshots" / snapshot
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            database = folder / "corpus.sqlite3"
            if manifest["snapshot"] != snapshot or digest(database) != manifest["database_sha256"]:
                raise ValueError("Corpus snapshot integrity failure")
            self.db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
        else:
            self.db = sqlite3.connect(self.directory / "corpus.sqlite3", timeout=60)
        self.db.row_factory = sqlite3.Row
        if not snapshot:
            self.db.executescript("""
        CREATE TABLE IF NOT EXISTS models(
          id TEXT PRIMARY KEY, sha TEXT, family TEXT, metadata TEXT, status TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS modules(
          id TEXT PRIMARY KEY, model_id TEXT, hash TEXT, section TEXT, part_count INTEGER,
          metadata TEXT, check_status TEXT DEFAULT 'unverified', report TEXT);
        CREATE INDEX IF NOT EXISTS modules_hash ON modules(hash);
        CREATE INDEX IF NOT EXISTS modules_model ON modules(model_id);
        """)

    def index(self, catalog, root, limit=None):
        rows = json.loads(Path(catalog).read_text(encoding="utf-8"))["models"]
        rows = rows if limit is None else rows[:limit]
        root = Path(root).resolve()
        write_json(self.directory / "progress.json", dict(status="running", completed=0, total=len(rows)))
        for number, row in enumerate(rows):
            path = (root / row["file"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Corpus path escapes source")
            metadata = dict(row, community_reconstruction=True, official_structure_verified=False)
            status, error = "parsed", None
            actual = None
            try:
                actual = digest(path)
                if row.get("sha256") and actual != row["sha256"]:
                    raise ValueError("Source hash differs from catalog")
                cached = self.db.execute("SELECT sha,status FROM models WHERE id=?", (row["id"],)).fetchone()
                previous = self.db.execute("SELECT metadata FROM models WHERE id=?", (row["id"],)).fetchone()
                if cached and cached["sha"] == actual and cached["status"] == "parsed" and json.loads(previous[0]).get("hash_version") == 4:
                    continue
                with ZipFile(path) as archive:
                    text = archive.read("model.ldr", pwd=PASSWORD).decode("utf-8-sig")
                    metadata["archive_members"] = [dict(path=i.filename, bytes=i.file_size, encrypted=bool(i.flag_bits & 1)) for i in archive.infolist()]
                assembly = Assembly(text, actual)
                graph = assembly.graph()
                licenses = re.findall(r"(?im)^0\s+!LICENSE\s+(.+)$", text)
                metadata.update(hash_version=4, license_evidence=sorted(set(licenses)), license_status="declared" if licenses else "unknown",
                    transform_warnings=len(graph["warnings"]), package_manifest=embedded_packages(path, self.directory / "packages" / actual))
                self.db.execute("DELETE FROM modules WHERE model_id=?", (row["id"],))
                for section in assembly.sections:
                    if not assembly.is_module(section):
                        continue
                    module_text = extract_module(assembly, section)
                    module = Assembly(module_text)
                    nodes = [n for n in module.graph()["instances"] if n["kind"] == "part"]
                    if not nodes:
                        continue
                    identity = sha256((row["id"] + "/" + section).encode()).hexdigest()[:24]
                    hashes = dependency_hashes(metadata["package_manifest"])
                    module_hash = canonical_module(assembly, section, dependencies=hashes)
                    text_hash = sha256(module_text.encode("utf-8")).hexdigest()
                    source = self.directory / "modules" / (text_hash + ".mpd")
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text(module_text, encoding="utf-8", newline="\n")
                    positions = np.array([n["world"] for n in nodes])[:, :3, 3]
                    description = dict(name_zh=row["name_zh"], types=row.get("vehicle_types", []),
                        brands=[b["id"] for b in row.get("brands", [])], source_sha256=actual,
                        license_evidence=metadata["license_evidence"], tag_status="unverified_name_hint",
                        name_hint=section, origin_span_ldu=(positions.max(0)-positions.min(0)).tolist(),
                        file=str(source.relative_to(self.directory)), file_sha256=text_hash, package="packages/" + actual,
                        root_module=section == assembly.root,
                        parts=sorted({n["part"] for n in nodes}), ports_status="unverified", training_eligible=False)
                    self.db.execute("INSERT OR REPLACE INTO modules(id,model_id,hash,section,part_count,metadata,check_status) VALUES(?,?,?,?,?,?,?)",
                        (identity, row["id"], module_hash, section, len(nodes), json.dumps(description, ensure_ascii=False), "unverified"))
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
            family = re.sub(r"-\d+$", "", row.get("set_num", row["id"]))
            self.db.execute("INSERT OR REPLACE INTO models VALUES(?,?,?,?,?,?)",
                (row["id"], actual, family, json.dumps(metadata, ensure_ascii=False), status, error))
            self.db.commit()
            if number % 25 == 0:
                write_json(self.directory / "progress.json", dict(status="running", completed=number+1, total=len(rows)))
        result = self.summary()
        write_json(self.directory / "inventory.json", result)
        write_json(self.directory / "progress.json", dict(status="completed", completed=len(rows), total=len(rows), snapshot=result["snapshot"]))
        return result

    def summary(self):
        statuses = dict(self.db.execute("SELECT status,COUNT(*) FROM models GROUP BY status").fetchall())
        modules, unique = self.db.execute("SELECT COUNT(*), COUNT(DISTINCT hash) FROM modules").fetchone()
        return dict(schema=SCHEMA, models=sum(statuses.values()), statuses=statuses, modules=modules,
                    unique_modules=unique, training_eligible=sum(bool(json.loads(row[0]).get("training_eligible")) for row in self.db.execute("SELECT metadata FROM modules")),
                    snapshot=self.snapshot(), appearance="unverified", physical="unverified")

    def snapshot(self):
        if self.frozen_snapshot:
            return self.frozen_snapshot
        models = list(map(tuple, self.db.execute("SELECT id,sha,status FROM models ORDER BY id")))
        modules = list(map(tuple, self.db.execute("SELECT id,hash,check_status,metadata FROM modules ORDER BY id")))
        return sha256(json.dumps([SCHEMA, models, modules], separators=(",", ":")).encode()).hexdigest()

    def module(self, identity):
        row = self.db.execute("SELECT * FROM modules WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("Unknown module")
        item = {**dict(row), "metadata": json.loads(row["metadata"])}
        expected = item["metadata"].get("file_sha256")
        if expected and digest(self.directory / item["metadata"]["file"]) != expected:
            raise ValueError("Module content differs from snapshot")
        review = item["metadata"].get("source_review")
        if item["metadata"].get("source_review_status") == "approved":
            path = (self.directory / review["file"]).resolve() if review else None
            if not path or not path.is_relative_to(self.directory.resolve()) or digest(path) != review["sha256"]:
                raise ValueError("Source review evidence differs from snapshot")
        source = self.db.execute("SELECT metadata FROM models WHERE id=?", (item["model_id"],)).fetchone()
        package = (self.directory / item["metadata"]["package"]).resolve()
        if not package.is_relative_to(self.directory.resolve()):
            raise ValueError("Package path escapes corpus")
        for entry in json.loads(source[0]).get("package_manifest", []):
            path = (package / entry["path"]).resolve()
            if not path.is_relative_to(package) or not path.is_file() or digest(path) != entry["sha256"]:
                raise ValueError("Package dependency differs from snapshot")
        return item

    def repair_cache_newlines(self):
        """Migrate Windows caches only when LF bytes match their original digest."""
        if self.frozen_snapshot:
            raise ValueError("Cannot migrate a frozen snapshot")
        repaired, inspected = 0, set()
        for row in self.db.execute("SELECT metadata FROM modules"):
            meta = json.loads(row[0])
            path = (self.directory / meta["file"]).resolve()
            if not path.is_relative_to(self.directory.resolve()):
                raise ValueError("Module path escapes corpus")
            if path in inspected:
                continue
            inspected.add(path)
            raw = path.read_bytes()
            if sha256(raw).hexdigest() == meta["file_sha256"]:
                continue
            normalized = raw.replace(b"\r\n", b"\n")
            if sha256(normalized).hexdigest() != meta["file_sha256"]:
                raise ValueError("Cache corruption is not a newline conversion: " + str(path))
            path.write_bytes(normalized)
            repaired += 1
        for row in self.db.execute("SELECT id,metadata FROM models").fetchall():
            meta = json.loads(row["metadata"])
            if meta.get("hash_version") == 3:
                meta["hash_version"] = 4
                self.db.execute("UPDATE models SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), row["id"]))
        self.db.commit()
        result = dict(files=len(inspected), repaired=repaired, **self.summary())
        write_json(self.directory / "cache-migration.json", result)
        write_json(self.directory / "inventory.json", self.summary())
        write_json(self.directory / "progress.json", dict(status="completed", completed=result["models"], total=result["models"], snapshot=result["snapshot"]))
        return result

    def review_source(self, identity, source_root, native_root, *, reviewer, notes):
        """Record a reproducible provenance/geometry audit, not official certification."""
        from .assembly_catalog import NativeCatalog, validate
        if self.frozen_snapshot or not reviewer.strip() or not notes.strip():
            raise ValueError("Review needs a writable corpus, reviewer and rationale")
        item = self.module(identity)
        source_row = self.db.execute("SELECT * FROM models WHERE id=?", (item["model_id"],)).fetchone()
        source = json.loads(source_row["metadata"])
        root = Path(source_root).resolve()
        path = (root / source["file"]).resolve()
        if not path.is_relative_to(root) or digest(path) != source_row["sha"]:
            raise ValueError("Review source differs from indexed archive")
        with ZipFile(path) as archive:
            text = archive.read("model.ldr", pwd=PASSWORD).decode("utf-8-sig")
        original = Assembly(text)
        extracted = extract_module(original, item["section"]).encode("utf-8")
        if sha256(extracted).hexdigest() != item["metadata"]["file_sha256"]:
            raise ValueError("Reviewed module differs from source section")
        licenses = sorted({s.strip() for s in re.findall(r"(?im)^0\s+!LICENSE\s+(.+)$", text)})
        recognized = bool(licenses) and all(re.fullmatch(
            r"(?:Redistributable under CCAL version 2\.0\s*:\s*see CAreadme\.txt|"
            r"(?:Redistributable|Licensed) under CC BY 4\.0\s*:\s*see (?:LICENSE\.md|CAreadme\.txt))", s, re.I) for s in licenses)
        catalog = NativeCatalog(native_root, [self.directory / item["metadata"]["package"]])
        assembly = Assembly(extracted.decode("utf-8"))
        report = validate(assembly.graph(), catalog)
        provenance = bool(source.get("author") and source.get("source_url", "").startswith("https://"))
        approved = recognized and provenance and report["status"] == "pass"
        audit = dict(schema="assembly-source-review.v1", kind="automated_provenance_and_geometry",
            reviewer=reviewer, notes=notes, module_id=identity, model_id=item["model_id"],
            source_sha256=source_row["sha"], module_sha256=sha256(extracted).hexdigest(),
            source_url=source.get("source_url"), author=source.get("author"), licenses=licenses,
            recognized_license=recognized, parts=[catalog.part(p) for p in item["metadata"]["parts"]],
            structure=report, status="approved" if approved else "unverified",
            official_structure_verified=False, appearance="unverified", physical="unverified",
            limitations=["Checks cover digital connections and collisions only; semantic module labels remain hints.",
                          "Embedded license declarations are source evidence, not a legal opinion."])
        payload = json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        destination = self.directory / "reviews" / (sha256(payload).hexdigest() + ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        meta = item["metadata"]
        meta.update(source_review_status=audit["status"], source_review=dict(file=destination.relative_to(self.directory).as_posix(), sha256=digest(destination)), training_eligible=approved)
        self.db.execute("UPDATE modules SET check_status=?,report=?,metadata=? WHERE id=?",
            (report["status"], json.dumps(report), json.dumps(meta, ensure_ascii=False), identity))
        self.db.commit()
        return audit

    def exclusions(self, *, exclude_models=(), exclude_families=(), exclude_hashes=()):
        """Exclude target families and their duplicate modules across all sources."""
        models, families, hashes = set(exclude_models), set(exclude_families), set(exclude_hashes)
        for row in self.db.execute("SELECT id,family FROM models"):
            if row["id"] in models:
                families.add(row["family"])
        models.update(row["id"] for row in self.db.execute("SELECT id,family FROM models")
                      if row["family"] in families)
        hashes.update(row["hash"] for row in self.db.execute("SELECT model_id,hash FROM modules")
                      if row["model_id"] in models)
        return dict(models=sorted(models), families=sorted(families), hashes=sorted(hashes))

    def allowed(self, item, exclusions):
        if item["model_id"] in exclusions["models"] or item["hash"] in exclusions["hashes"]:
            return False
        if not exclusions["hashes"]:
            return True
        # A parent assembly must not smuggle an excluded child module into graft.
        assembly = Assembly.read(self.directory / item["metadata"]["file"])
        source = self.db.execute("SELECT metadata FROM models WHERE id=?", (item["model_id"],)).fetchone()
        dependencies = dependency_hashes(json.loads(source[0]).get("package_manifest", []))
        blocked = set(exclusions["hashes"])
        return not any(canonical_module(assembly, section, dependencies=dependencies) in blocked
                       for section in assembly.sections if assembly.is_module(section))

    def retrieval_diagnostics(self, types=()):
        available = sorted({kind for row in self.db.execute("SELECT metadata FROM modules")
                            for kind in json.loads(row[0]).get("types", [])})
        unknown = sorted(set(types) - set(available))
        return dict(reason="unknown_vehicle_types" if unknown else "no_matching_modules",
            types_meaning="Vehicle body categories, not module kinds; omit types to search all categories.",
            unknown_types=unknown, available_types=available,
            query_scope="Model/submodel names and brand/category tags; semantic roles are not verified labels.",
            recovery="Correct the category filter or shorten the name query. If no suitable modules remain, "
                "use search_parts and query_part to construct candidates with native parts.",
            exclusions_unchanged=True, filters_relaxed=False)

    def retrieve(self, *, types=(), min_parts=1, max_parts=500, exclude_models=(), exclude_families=(),
                 exclude_hashes=(), limit=12, verified_only=False, query=""):
        if not 1 <= limit <= 100 or not 1 <= min_parts <= max_parts <= 20000:
            raise ValueError("Invalid retrieval limits")
        excluded = self.exclusions(exclude_models=exclude_models, exclude_families=exclude_families,
                                   exclude_hashes=exclude_hashes)
        terms = set(re.findall(r"[^\W_]+", query.casefold()))
        found = []
        for row in self.db.execute("SELECT * FROM modules WHERE part_count BETWEEN ? AND ? ORDER BY id", (min_parts, max_parts)):
            if row["model_id"] in excluded["models"] or row["hash"] in excluded["hashes"] or (verified_only and row["check_status"] != "pass"):
                continue
            item = {**dict(row), "metadata": json.loads(row["metadata"])}
            meta = item["metadata"]
            if types and not set(types).intersection(meta["types"]):
                continue
            text = " ".join([meta["name_zh"], row["section"],
                *meta.get("brands", []), *meta.get("types", [])]).casefold()
            words = set(re.findall(r"[^\W_]+", text))
            matched = sorted(term for term in terms if term in words or
                (re.search(r"[\u3400-\u9fff]", term) and term in text))
            if terms and not matched:
                continue
            score = len(matched)
            item["retrieval"] = dict(score=score, matched_terms=matched)
            found.append((score, item))
        found.sort(key=lambda r: (-r[0], r[1]["part_count"], r[1]["id"]))
        seen, result = set(), []
        for _, item in found:
            if item["hash"] not in seen and self.allowed(item, excluded):
                seen.add(item["hash"])
                result.append(item)
            if len(result) == limit:
                break
        return result

    def verify(self, identity, native_root):
        from .assembly_catalog import NativeCatalog, validate
        item = self.module(identity)
        meta = item["metadata"]
        catalog = NativeCatalog(native_root, [self.directory / meta["package"]])
        assembly = Assembly.read(self.directory / meta["file"])
        report = validate(assembly.graph(), catalog)
        meta["training_eligible"] = report["status"] == "pass" and meta.get("source_review_status") == "approved"
        self.db.execute("UPDATE modules SET check_status=?,report=?,metadata=? WHERE id=?",
            (report["status"], json.dumps(report), json.dumps(meta, ensure_ascii=False), identity))
        self.db.commit()
        return report

    def freeze(self):
        """Create an immutable retrieval database and source-hashed manifest."""
        identity = self.snapshot()
        directory = self.directory / "snapshots" / identity
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "corpus.sqlite3"
        if not destination.exists():
            with sqlite3.connect(destination) as target:
                self.db.backup(target)
        manifest = dict(schema=SCHEMA, snapshot=identity, database_sha256=digest(destination),
            models=[dict(r) for r in self.db.execute("SELECT id,sha,family,status,error FROM models ORDER BY id")],
            modules=[dict(r) for r in self.db.execute("SELECT id,model_id,hash,check_status FROM modules ORDER BY id")])
        path = directory / "manifest.json"
        if not path.exists():
            write_json(path, manifest)
        return dict(snapshot=identity, manifest=str(path), database=str(destination))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(Corpus(args.output).index(args.catalog, args.root, args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
