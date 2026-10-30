"""Bounded visual context with source hashes and explicit reference handedness."""
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageOps

from .common import digest
from .assembly_requirements import identity


VERSION = "assembly-reference-scope.v1"
MAX_VIEWS = 8


def reference_scope(task, views, *, limit=MAX_VIEWS):
    if not 1 <= limit <= MAX_VIEWS:
        raise ValueError("Reference scope limit must be between one and eight")
    name = task.get("id", "")
    group = name.split("_", 1)[0]
    tags, preferred = {
        "wheel": ({"wheel", "wheels"}, ["front_running_gear" if "front" in name else "rear_running_gear"]),
        "door": ({"door", "doors", "glazing", "hinges"}, [name + "_closed", name + "_open", "scissor_hinge"]),
        "steering": ({"steering", "wheels"}, ["front_running_gear", "front", "top"]),
        "suspension": ({"suspension", "wheels"}, ["front_running_gear", "rear_running_gear"]),
        "chassis": ({"chassis", "underbody", "body"}, ["top", "left", "right"]),
        "cabin": ({"cabin", "interior", "glazing"}, ["dashboard", "seats_pedals", "passenger_seat", "windscreen"]),
        "hood": ({"front", "hinges"}, ["front_cover_open", "front_cabin_join", "top"]),
        "headlamp": ({"front", "lights", "hinges"}, ["popup_lamps", "headlamps", "front"]),
        "front": ({"front", "lights"} if name == "front_lights" else {"front"},
                  ["headlamps", "front", "front_cabin_join"] if name == "front_lights"
                  else ["front_cabin_join", "front", "front_left", "front_right"]),
        "rear": ({"rear", "lights"} if name == "rear_lights" else {"rear", "powertrain"},
                 ["taillamps", "back", "rear_cabin_join"] if name == "rear_lights"
                 else ["rear_clamshell", "rear_cover_open", "powertrain_cargo"] if name == "rear_cover"
                 else ["rear_cabin_join", "back", "rear_left", "rear_right"]),
        "signature": ({"body", "front", "rear", "doors"}, ["side_intake", "popup_lamps", "rear_clamshell"]),
    }.get(group, ({name}, []))
    tokens = set(name.split("_"))
    side = next((s for s in ("left", "right") if s in tokens), None)
    end = next((s for s in ("front", "rear") if s in tokens), None)
    ranked = []
    for view, row in views.items():
        text = " ".join((view, row.get("camera", ""))).lower()
        sides = set(re.findall(r"\b(left|right)\b", text.replace("_", " ")))
        relation = ("same_side" if side and side in sides else "opposite_side"
                    if side and sides else "not_side_specific")
        if group == "door" and relation == "opposite_side" and row.get("category") == "detail":
            continue
        matches = bool(tags & set(row.get("modules", [])))
        if view == "overview":
            score, reason = 1000, "identity_anchor"
        elif view in preferred:
            score, reason = 900 - preferred.index(view) * 10, "module_detail"
        elif side and view == side:
            score, reason = 850, "side_elevation"
        elif side and end and view == end + "_" + side:
            score, reason = 840, "module_quarter_view"
        elif name == view:
            score, reason = 830, "module_view"
        elif matches:
            score, reason = (600 if row.get("category") == "detail" else 400), "module_tag"
        elif view in {"front", "back", "left", "right", "top"}:
            score, reason = 100, "whole_vehicle_context"
        else:
            continue
        if relation == "opposite_side" and view != "overview":
            score -= 700
        if group == "wheel" and end and view.startswith(("rear_" if end == "front" else "front_")):
            score -= 500
        ranked.append((-score, view, dict(reason=reason, relation=relation)))
    ranked.sort(key=lambda row: (row[0], row[1]))
    selected = {view: evidence for _, view, evidence in ranked[:limit]}
    return dict(schema=VERSION, module=name, views=selected,
                available_views=list(views), omitted_views=[view for view in views if view not in selected],
                instructions="The overview fixes identity. Opposite-side details are comparative evidence, "
                    "not proof of handedness. Use view_reference(view) for full-resolution originals; "
                    "omitted views remain available. Hidden structures remain inferred.")


def prepare_module_reference_sheets(directory, views, images, modules):
    """Generate immutable, bounded contact sheets; keep every original addressable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    sheets = {}
    for module in modules:
        scope = reference_scope(dict(id=module), views)
        sources = {}
        for view in scope["views"]:
            if view not in images:
                raise ValueError("Missing module reference: " + view)
            path, expected = images[view]
            if not Path(path).is_file() or digest(path) != expected:
                raise ValueError("Module reference checksum mismatch: " + view)
            sources[view] = expected
        if not sources:
            raise ValueError("Module reference scope is empty: " + module)
        version = identity(dict(scope=scope, sources=sources, cell=320, columns=4, quality=93))
        destination = directory / (version + ".jpg")
        columns, cell = min(4, len(sources)), 320
        height = ((len(sources) + columns - 1) // columns) * (cell + 32)
        sheet = Image.new("RGB", (columns * cell, height), "white")
        draw = ImageDraw.Draw(sheet)
        for position, view in enumerate(sources):
            left, top = position % columns * cell, position // columns * (cell + 32)
            with Image.open(images[view][0]) as opened:
                thumb = ImageOps.contain(ImageOps.exif_transpose(opened).convert("RGB"), (cell, cell))
                sheet.paste(thumb, (left + (cell - thumb.width) // 2, top + (cell - thumb.height) // 2))
            draw.text((left + 4, top + cell + 4), view, fill="black")
        sheet.save(destination, quality=93)
        sheets[module] = dict(scope=scope, file=str(destination.resolve()), sha256=digest(destination),
                              source_sha256=sources, pixels=list(sheet.size))
    return sheets
