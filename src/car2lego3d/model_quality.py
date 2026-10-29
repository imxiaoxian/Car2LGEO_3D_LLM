"""Measured scene diagnostics for model refinement, independent of visual acceptance."""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from .common import digest, write_json
from .vehicle import measurement, tyre_diameter


def complete_seam_sampling(seam):
    """Accept a short contact only with a complete edge-by-edge sampling record."""
    sampling = seam.get("sampling")
    if sampling is None:
        return seam["sampled_length_m"] >= .02
    if (not isinstance(sampling, dict) or sampling.get("schema") != "seam-sampling.v1"
            or sampling.get("maximum_step_m") != .01):
        return False
    spans = sampling.get("edges")
    if not isinstance(spans, list) or not spans or len(spans) != seam.get("source_edge_count"):
        return False
    offset, total = 0, 0
    for span in spans:
        if not isinstance(span, dict):
            return False
        length = span.get("length_m")
        if (not isinstance(length, (int, float)) or isinstance(length, bool)
                or not math.isfinite(length) or length <= 1e-8):
            return False
        count = max(2, math.ceil(length / .01)) + 1
        if span.get("offset") != offset or span.get("count") != count:
            return False
        offset += count
        total += length
    return (offset == seam["sample_count"]
        and math.isclose(total, seam["sampled_length_m"], rel_tol=1e-6, abs_tol=1e-9))


def assess_model(directory, spec, body_style=None):
    from .baseline_detail import detail_standard
    standard = detail_standard(spec)
    directory = Path(directory)
    metrics_path = directory / "scene-metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    objects = metrics["objects"]
    checks = []

    def check(name, actual, target, tolerance, unit="m"):
        error = abs(actual - target)
        checks.append(dict(name=name, actual=actual, target=target, tolerance=tolerance,
            unit=unit, error=error, passed=bool(np.isfinite(actual) and error <= tolerance)))

    def extent(items):
        bounds = np.asarray([o["bounds_m"] for o in items], dtype=float)
        return np.min(bounds[:, 0], axis=0), np.max(bounds[:, 1], axis=0)

    low, high = np.asarray(metrics["bounds_m"])
    length, width, height = [measurement(spec, k) / 1000 for k in ("length", "width", "height")]
    check("overall_length", float(high[0] - low[0]), length, length * .04)
    check("front_origin", float(low[0]), 0, .03)
    bodies = [o for o in objects if o.get("role") == "body"]
    open_wheel = body_style == "f1_race"
    if bodies and not open_wheel:
        bmin, bmax = extent(bodies)
        check("body_width_excluding_mirrors", float(bmax[1] - bmin[1]), width, width * .06)
    elif not bodies:
        checks.append(dict(name="body_role", passed=False, issue="Main shell lacks car_role=body"))
    roofs = [o for o in objects if o.get("role") == "roof"]
    if roofs:
        _, roof_max = extent(roofs)
        check("roof_height", float(roof_max[2]), height, height * .05)
    elif body_style not in {"f1_race", "convertible"}:
        checks.append(dict(name="roof_role", passed=False, issue="Roof lacks car_role=roof"))
    for obj in objects:
        if obj.get("role") in {"door_panel", "door_frame", "side_window"}:
            omin, omax = np.asarray(obj["bounds_m"])
            checks.append(dict(name=obj["name"] + ".cabin_envelope", passed=bool(
                omin[0] >= -.03 and omax[0] <= length + .03 and omin[2] >= -.015 and omax[2] <= height + .05
                and max(abs(omin[1]), abs(omax[1])) <= width / 2 + .05), bounds_m=obj["bounds_m"]))
        if obj.get("role") == "body" and obj.get("boundary_edges") == 0:
            checks.append(dict(name=obj["name"] + ".outward_volume", passed=obj.get("signed_volume_m3", 0) > 0,
                               signed_volume_m3=obj.get("signed_volume_m3")))
    named = {o["name"]: o for o in objects}
    axle_centres = {}
    for axle in ("front", "rear"):
        diameter = tyre_diameter(spec, axle)
        track, tyre_width = measurement(spec, axle + "_track"), measurement(spec, axle + "_tyre_width")
        front = measurement(spec, "front_overhang")
        target_x = None if front is None else (front + (measurement(spec, "wheelbase") if axle == "rear" else 0)) / 1000
        for side, sign in (("left", -1), ("right", 1)):
            name = f"tyre_{axle}_{side}"
            if name not in named:
                checks.append(dict(name=name, passed=False, issue="Missing named tyre mesh"))
                continue
            wlow, whigh = np.asarray(named[name]["bounds_m"])
            size, centre = whigh - wlow, (whigh + wlow) / 2
            axle_centres[axle, side] = centre
            check(name + ".ground", float(wlow[2]), 0, .015)
            if target_x is not None:
                check(name + ".axle_x", float(centre[0]), target_x, .025)
            if track is not None:
                check(name + ".track_y", float(centre[1]), sign * track / 2000, .02)
            if diameter is not None:
                for axis in (0, 2):
                    check(name + f".diameter_{axis}", float(size[axis]), diameter / 1000, .015)
            if tyre_width is not None:
                check(name + ".width", float(size[1]), tyre_width / 1000, .015)
    for side in ("left", "right"):
        if ("front", side) in axle_centres and ("rear", side) in axle_centres:
            check(side + ".wheelbase", float(axle_centres["rear", side][0] - axle_centres["front", side][0]),
                  measurement(spec, "wheelbase") / 1000, .025)
    contacts = metrics.get("door_window_contacts", [])
    if body_style not in {"f1_race", "convertible"}:
        windows = {o["name"] for o in objects if o.get("role") == "side_window"}
        checked_windows = {c["window"] for c in contacts}
        minimum = 2 if standard is None or standard["separate_door_glass_required"] else 0
        checks.append(dict(name="door_window_assemblies", passed=bool(len(windows) >= minimum
            and checked_windows == windows and len({c["door"] for c in contacts}) >= min(2, len(windows))),
            applicable=bool(windows) or minimum > 0,
            basis="baseline_io" if standard else "generic_vehicle",
            window_count=len(windows), measured_windows=sorted(checked_windows)))
    for contact in contacts:
        distances = contact["lower_edge_anchor_distances_m"]
        checks.append(dict(name=contact["window"] + ".door_contact", passed=bool(
            len(distances) == 2 and all(d is not None and d <= .02 for d in distances)),
            distances_m=distances, door=contact["door"], tolerance_m=.02))
    perimeters = metrics.get("door_perimeter_contacts", [])
    expected_perimeters = {o["name"] for o in objects if o.get("role") in {"side_window", "door_panel"}}
    if expected_perimeters:
        checks.append(dict(name="door_perimeter_evidence", passed=(
            {row["source"] for row in perimeters} == expected_perimeters),
            issue="Complete panel and glass boundaries need measurement, not only two lower anchors"))
    for perimeter in perimeters:
        distances = perimeter["distances_m"]
        measured = (perimeter.get("boundary_status") == "measured" and bool(perimeter["targets"])
            and len(distances) >= 3 and len(distances) == perimeter["sample_count"]
            and perimeter["sampled_length_m"] >= .02
            and all(d is not None and np.isfinite(d) for d in distances))
        maximum = max(distances) if measured else None
        checks.append(dict(name=perimeter["source"] + ".perimeter_seating",
            passed=bool(measured and maximum <= .012), max_gap_m=maximum, tolerance_m=.012,
            boundary_status=perimeter.get("boundary_status"),
            sampled_length_m=perimeter["sampled_length_m"], sample_count=len(distances),
            worst_samples=perimeter.get("worst_samples", []), targets=perimeter["targets"]))
    for visibility in metrics.get("glass_visibility", []):
        checks.append(dict(name=visibility["window"] + ".visibility", passed=visibility["visible_fraction"] >= .8,
                           visible_fraction=visibility["visible_fraction"], first_hits=visibility["first_hits"]))
    for seam in metrics.get("surface_connections", []):
        distances = seam["distances_m"]
        sampling_complete = complete_seam_sampling(seam)
        measured = bool(seam["targets"] and len(distances) >= 3 and sampling_complete
                        and seam.get("boundary_width_coverage", 1) >= .95
                        and len(distances) == seam["sample_count"]
                        and all(d is not None and np.isfinite(d) for d in distances))
        maximum = max(distances) if measured else None
        checks.append(dict(name=seam["source"] + ".connection." + seam["seam"],
            passed=bool(measured and maximum <= .012), max_gap_m=maximum, tolerance_m=.012,
            sampled_length_m=seam["sampled_length_m"], sample_count=seam["sample_count"],
            sampling_complete=sampling_complete,
            boundary_width_coverage=seam.get("boundary_width_coverage"),
            worst_samples=seam.get("worst_samples", []),
            targets=seam["targets"], region=seam["region"]))
    triangles = metrics.get("triangles", 0)
    checks.append(dict(name="nonempty_mesh", actual=triangles, passed=triangles > 0))
    performance = dict(triangles=triangles, advisory_triangle_budget=250000,
        status="warning" if triangles > 250000 else "within_advisory_budget",
        blocks_geometry=False, detail_reduction_authorized=False)
    for view in ("front", "back", "left", "right", "three-quarter", "rear-quarter"):
        path = directory / f"render-{view}.png"
        if not path.is_file():
            checks.append(dict(name=view + ".pixels", passed=False, issue="Missing rendered view"))
            continue
        pixels = np.asarray(Image.open(path).convert("RGBA"))
        visible = pixels[:, :, 3] > 128
        fraction = float(visible.mean())
        clipped = bool(visible[0].any() or visible[-1].any() or visible[:, 0].any() or visible[:, -1].any())
        checks.append(dict(name=view + ".pixels", visible_fraction=fraction, clipped=clipped,
                           passed=.015 < fraction < .85 and not clipped))
    failures = [c for c in checks if not c["passed"]]
    result = dict(schema_version=3, status="pass" if not failures else "needs_revision", checks=checks,
        performance=performance, detail_standard=standard,
        failed_checks=len(failures), metrics_sha256=digest(metrics_path), appearance_accepted=False,
        limitations=["Checks measure scene geometry against model-estimated priors; they do not establish visual likeness.",
                     "Door perimeter distances do not validate hinge travel, physical assembly or manufacturing fit.",
                     "Seam distances measure declared source edges to target surfaces, not tangency, penetration or watertightness."])
    write_json(directory / "model-quality.json", result)
    return result


def new_failures(before, after):
    previous = {c["name"] for c in before["checks"] if not c["passed"]}
    failures = {c["name"] for c in after["checks"] if not c["passed"]} - previous
    # Splitting a rail is valid; removing a previously measured contact is not.
    measured_after = {c["name"].split("__", 1)[0] for c in after["checks"] if ".connection." in c["name"]}
    missing = {c["name"] for c in before["checks"] if c["passed"] and ".connection." in c["name"]
               and c["name"].split("__", 1)[0] not in measured_after}
    return sorted(failures | missing)
