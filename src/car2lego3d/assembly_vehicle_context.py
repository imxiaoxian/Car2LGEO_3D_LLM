"""Compact whole-vehicle geometry and verified visual inputs for the website."""
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .common import digest, write_json
from .references import load_references
from .assembly_reference_context import prepare_module_reference_sheets, reference_scope


def module_vehicle_context(vehicle, task):
    """Keep full geometry addressable while supplying the active module's scope."""
    name = task.get("id", "")
    group = name.split("_", 1)[0]
    terms = {
        "wheel": ("wheel", "tyre", "rim", "hub", "brake", "axle", "bearing"),
        "chassis": ("chassis", "crossmember", "underbody"),
        "steering": ("steering", "upright", "wishbone", "ball_joint"),
        "suspension": ("suspension", "damper", "spring", "wishbone", "shock", "antiroll"),
        "door": ("door", "side_window", "quarter_window", "glass_gasket"),
        "cabin": ("cabin", "seat", "dashboard", "steering_wheel", "console", "windscreen", "pillar", "roof"),
        "front": ("front", "headlamp", "lamp", "hood", "cowl", "bumper"),
        "rear": ("rear", "tail_lamp", "engine", "cover", "exhaust"),
        "hood": ("hood", "front_compartment", "cowl"),
        "headlamp": ("headlamp", "lamp", "reflector"),
    }.get(group, (name,))
    roles = {role: item for role, item in vehicle.get("roles", {}).items()
             if any(term in role for term in terms)}
    views = vehicle.get("reference_views", {})
    sheet = vehicle.get("module_reference_sheets", {}).get(name)
    scope = sheet["scope"] if sheet else reference_scope(task, views)
    return dict(schema="assembly-module-context.v2", module=name,
        source_node_count=vehicle["source_node_count"], summary_only=True,
        coordinates=vehicle["coordinates"], roles=roles,
        full_context="vehicle_context", specifications="vehicle_context:/specifications",
        semantic_geometry_available=vehicle["semantic_geometry_available"],
        reference_views={view: views[view] for view in scope["views"]},
        reference_scope=scope,
        contact_sheet={key: sheet[key] for key in ("file", "sha256")} if sheet else vehicle["contact_sheet"],
        instructions=vehicle["instructions"])


def prepare_vehicle_context(directory, index, references, specifications, *, modules=()):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    roles = defaultdict(list)
    for name, node in index["nodes"].items():
        roles[node["role"]].append((name, node))
    summary = {}
    for role, members in roles.items():
        bounds = [node["world_bounds_m"] for _, node in members if "world_bounds_m" in node]
        item = dict(count=len(members), example_ids=[name for name, _ in members[:8]],
                    full_index_pointer="semantic_index:/nodes")
        if bounds:
            boxes = np.array(bounds)
            item["world_bounds_m"] = [boxes[:, 0].min(axis=0).tolist(), boxes[:, 1].max(axis=0).tolist()]
        item["joints"] = [dict(id=name, **node["joint"]) for name, node in members if node.get("joint")]
        summary[role] = item
    manifest, images = load_references(references)
    rows = manifest.get("shots") or manifest["views"]
    columns, cell = 4, 320
    sheet = Image.new("RGB", (columns * cell, ((len(images) + columns - 1) // columns) * (cell + 32)), "white")
    draw = ImageDraw.Draw(sheet)
    for position, (view, path) in enumerate(images.items()):
        left, top = position % columns * cell, position // columns * (cell + 32)
        with Image.open(path) as opened:
            thumb = ImageOps.contain(opened.convert("RGB"), (cell, cell))
            sheet.paste(thumb, (left + (cell - thumb.width) // 2, top + (cell - thumb.height) // 2))
        draw.text((left + 4, top + cell + 4), view, fill="black")
    sheet_path = directory / "reference-contact-sheet.jpg"
    sheet.save(sheet_path, quality=93)
    reference_images = {view: (path, digest(path)) for view, path in images.items()}
    module_sheets = prepare_module_reference_sheets(directory / "module-references", rows, reference_images, modules)
    data = dict(schema="assembly-vehicle-context.v2", source_node_count=index["node_count"],
        summary_only=True, semantic_geometry_available="semantic:/nodes/<index>", roles=summary,
        coordinates=dict(source="metres, X front-to-rear, Y left-to-right, Z up",
            target="LDraw LDU, X front-to-rear, Y down, Z lateral",
            nominal_scale=12, mapping="[X,-Z,Y]/(12*0.0004); scale is flexible; never scale native parts"),
        specifications=specifications,
        reference_views={view: {key: row[key] for key in ("modules", "state", "category", "camera", "focus") if key in row}
                         for view, row in rows.items()},
        contact_sheet=dict(file=str(sheet_path.resolve()), sha256=digest(sheet_path)),
        module_reference_sheets=module_sheets,
        instructions="Use view_reference(view) for the original detailed image. The contact sheet is an overview only. "
            "Start with a structural plan in remember, retrieve three relevant candidates per module, search native parts, "
            "then create named .ldr modules with edit op=module and add parts in batches. "
            "Use a module/add alias and @alias to build nested modules in one atomic edit. "
            "Query module geometry before grafting; all previous part queries remain in known_evidence. Do not copy a complete held-out car. "
            "Keep standard part dimensions. Fit native wheel groups and mechanisms first, then body panels, windows and lights. "
            "Inspect actual geometry and connector data; missing evidence is unverified. Export incomplete work only as draft.")
    write_json(directory / "vehicle-context.json", data)
    return data, reference_images
