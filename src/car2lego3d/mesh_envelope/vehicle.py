"""Sourced physical dimensions, mesh calibration and LEGO constraint targets."""
import json
import math
from pathlib import Path

import numpy as np

from ..common import digest


UNITS = {name: "mm" for name in (
    "length", "width", "height", "wheelbase", "front_track", "rear_track",
    "front_overhang", "rear_overhang", "door_length", "door_panel_height",
    "door_front_x", "windscreen_bottom_x", "windscreen_bottom_width",
    "windscreen_top_width", "windscreen_slant_height", "seat_width",
    "engine_length", "engine_width", "engine_height")}
UNITS["windscreen_angle_to_horizontal"] = "deg"
UNITS["door_opening_angle"] = "deg"
for axle in ("front", "rear"):
    UNITS.update({f"{axle}_tyre_width": "mm", f"{axle}_tyre_aspect": "%",
                  f"{axle}_rim_diameter": "inch"})


def validate_spec(spec):
    if spec.get("schema_version") != 1 or not spec.get("model") or not spec.get("variant"):
        raise ValueError("Vehicle spec requires schema_version=1, model and variant.")
    sources, measurements = spec.get("sources", {}), spec.get("measurements", {})
    if set(measurements) - set(UNITS):
        raise ValueError(f"Unknown measurement fields: {sorted(set(measurements) - set(UNITS))}")
    for name, item in measurements.items():
        if item.get("unit") != UNITS[name]:
            raise ValueError(f"Wrong unit for {name}; expected {UNITS[name]}.")
        value, status = item.get("value"), item.get("status")
        if value is None:
            if status != "unknown":
                raise ValueError(f"Null {name} must be marked unknown.")
            continue
        allow_zero = name in ("front_overhang", "rear_overhang", "door_front_x", "windscreen_bottom_x")
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value < 0 or (value == 0 and not allow_zero)):
            raise ValueError(f"{name} must be finite and positive (coordinates may be zero).")
        if status not in ("manufacturer", "reported", "measured", "estimated"):
            raise ValueError(f"{name} requires manufacturer/reported/measured/estimated status.")
        source = sources.get(item.get("source"), {})
        if not source.get("title") or not (source.get("url") or source.get("file")):
            raise ValueError(f"{name} requires a traceable source.")
        if name == "windscreen_angle_to_horizontal" and value >= 90:
            raise ValueError("Windscreen angle must be between 0 and 90 degrees from horizontal.")
        if name == "door_opening_angle" and value > 180:
            raise ValueError("Door opening angle must not exceed 180 degrees.")
        if name.endswith("_tyre_aspect") and value > 100:
            raise ValueError("Tyre aspect ratio must not exceed 100 percent.")
    for name in ("length", "width", "height", "wheelbase"):
        if measurement(spec, name) is None:
            raise ValueError(f"Vehicle spec requires {name}.")
    length = measurement(spec, "length")
    if measurement(spec, "wheelbase") >= length:
        raise ValueError("Wheelbase must be shorter than vehicle length.")
    front, rear = measurement(spec, "front_overhang"), measurement(spec, "rear_overhang")
    if front is not None and rear is not None and abs(front + measurement(spec, "wheelbase") + rear - length) > 2:
        raise ValueError("Overhangs and wheelbase do not sum to length within 2 mm.")
    for name in ("front_track", "rear_track", "door_length", "door_front_x", "windscreen_bottom_x"):
        value = measurement(spec, name)
        maximum = measurement(spec, "width") if name.endswith("track") else length
        if value is not None and value >= maximum:
            raise ValueError(f"{name} exceeds its vehicle envelope.")
    for axle in ("front", "rear"):
        values = [measurement(spec, f"{axle}_{field}") for field in
                  ("tyre_width", "tyre_aspect", "rim_diameter")]
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError(f"{axle} tyre requires width, aspect and rim diameter together.")
    return spec


def load_spec(path):
    return validate_spec(json.loads(Path(path).read_text(encoding="utf-8")))


def measurement(spec, name):
    return spec.get("measurements", {}).get(name, {}).get("value")


def spec_record(path):
    spec = load_spec(path)
    return {"file": Path(path).name, "sha256": digest(path), "model": spec["model"],
            "variant": spec["variant"], "unknown_fields": [name for name in UNITS
                                                           if measurement(spec, name) is None]}


def tyre_diameter(spec, axle):
    width = measurement(spec, f"{axle}_tyre_width")
    if width is None:
        return None
    return measurement(spec, f"{axle}_rim_diameter") * 25.4 + 2 * width * measurement(spec, f"{axle}_tyre_aspect") / 100


def prompt_hint(spec):
    length, width, height = (measurement(spec, name) for name in ("length", "width", "height"))
    return f"Body length/width {length/width:.2f}, height/length {height/length:.2f}."


def calibrate_mesh(mesh, spec):
    """Input is already aligned to long X and up Z; output coordinates are mm."""
    target = np.array([measurement(spec, name) for name in ("length", "width", "height")])
    extents = mesh.extents
    if not np.isfinite(extents).all() or np.any(extents < 1e-8):
        raise ValueError("Cannot calibrate a degenerate mesh.")
    calibrated = mesh.copy()
    factors = target / extents
    transform = np.eye(4)
    transform[:3, :3] = np.diag(factors)
    transform[:3, 3] = -mesh.bounds[0] * factors
    calibrated.apply_transform(transform)
    calibrated.units = "mm"
    return calibrated, {"method": "axis-aligned bounding-box prior; independent X/Y/Z scaling",
                        "target_dimensions_mm": target.tolist(), "source_extents": extents.tolist(),
                        "mm_per_source_unit_xyz": factors.tolist(), "front_direction_verified": False,
                        "physical_measurement_verified": False,
                        "limitations": ["Calibration enforces a prior; it does not recover missing CAD detail.",
                                        "Mirrors, spoiler and mesh artefacts can bias the source bounding box."]}


def constraint_targets(spec, length_studs, scale_denominator=None):
    scale = 1 / scale_denominator if scale_denominator is not None else length_studs * 8 / measurement(spec, "length")
    targets = {name: measurement(spec, name) * scale for name, unit in UNITS.items()
               if unit == "mm" and measurement(spec, name) is not None}
    for axle in ("front", "rear"):
        diameter = tyre_diameter(spec, axle)
        if diameter is not None:
            targets[f"{axle}_tyre_diameter"] = diameter * scale
    return {"scale_denominator": 1 / scale, "model_mm_per_real_mm": scale,
            "target_model_mm": targets, "angle_reference": "horizontal ground plane",
            "windscreen_angle_degrees": measurement(spec, "windscreen_angle_to_horizontal"),
            "unknown_fields": [name for name in UNITS if measurement(spec, name) is None]}


def constrained_axles(spec, length, mesh_axles, scale_denominator=None):
    targets = constraint_targets(spec, length, scale_denominator)["target_model_mm"]
    wheelbase = targets["wheelbase"] / 8
    front_overhang, rear_overhang = targets.get("front_overhang"), targets.get("rear_overhang")
    if front_overhang is not None:
        desired_front = front_overhang / 8 - .5
        placement_source = "physical front overhang and wheelbase"
    elif rear_overhang is not None:
        desired_front = length - .5 - rear_overhang / 8 - wheelbase
        placement_source = "physical rear overhang and wheelbase"
    else:
        desired_front = (sum(mesh_axles) - wheelbase) / 2
        placement_source = "physical wheelbase; overhang split from mesh wheel hypotheses"
    # Native wheel arches occupy four studs; axle bricks require half-stud centres.
    candidates = [(a + .5, b + .5) for a in range(3, length-6) for b in range(a+6, length-3)]
    front, rear = min(candidates, key=lambda pair: (
        16 * ((pair[1] - pair[0]) - wheelbase) ** 2 + (pair[0] - desired_front) ** 2))
    return [front, rear], placement_source


def dimension_report(spec, fit):
    report = constraint_targets(spec, fit["length_studs"], fit.get("target_scale_denominator"))
    actual = {"length": fit["length_studs"] * 8, "width": fit["width_studs"] * 8,
              "wheelbase": (fit["axles_studs"][1] - fit["axles_studs"][0]) * 8,
              "front_overhang": (fit["axles_studs"][0] + .5) * 8,
              "rear_overhang": (fit["length_studs"] - .5 - fit["axles_studs"][1]) * 8,
              "front_track": (fit["width_studs"] - 1) * 8,
              "rear_track": (fit["width_studs"] - 1) * 8,
              "front_tyre_diameter": 24, "rear_tyre_diameter": 24,
              "front_tyre_width": 11.2, "rear_tyre_width": 11.2,
              "door_length": 32, "door_panel_height": 16,
              "windscreen_bottom_width": 48, "windscreen_top_width": 32}
    if "wheel_families" in fit:
        actual["height"] = fit["roof_top_plates"] * 3.2 - fit["ground_y_mm"]
        for axle, wheel in zip(("front", "rear"), fit["wheel_families"]):
            actual[f"{axle}_tyre_diameter"] = wheel["diameter_mm"]
            actual[f"{axle}_tyre_width"] = wheel["width_mm"]
            actual[f"{axle}_track"] = wheel["track_studs"] * 8
        actual["windscreen_top_width"] = fit.get("cabin_width_studs", 6) * 8
        actual["windscreen_bottom_width"] = fit.get("cabin_width_studs", 6) * 8
    errors = {name: {"target_mm": target, "fitted_mm": actual[name],
                     "error_mm": actual[name] - target,
                     "error_percent": None if target == 0 else 100 * (actual[name] / target - 1)}
              for name, target in report["target_model_mm"].items() if name in actual}
    # 65633's central front surface rises 27.5 LDU over approximately 70 LDU.
    native_angle = fit.get("fitted_windscreen_angle_degrees", math.degrees(math.atan2(27.5, 70)))
    report.update(model=spec["model"], variant=spec["variant"], dimensions=errors,
                  fitted_windscreen_angle_degrees=native_angle,
                  windscreen_angle_error_degrees=None if report["windscreen_angle_degrees"] is None or native_angle is None else
                  native_angle - report["windscreen_angle_degrees"],
                  enforced_constraints=["mesh length/width/height prior", "quantized wheelbase", "body width family"],
                  deferred_constraints=[name for name in ("height", "door_front_x", "windscreen_bottom_x",
                                                          "windscreen_slant_height")
                                        if measurement(spec, name) is not None and name not in actual],
                  dimensions_verified=False, appearance_accepted=False,
                  limitations=["Width is the rectangular body core, excluding arches, door skins and mirrors.",
                               "Tyres and glass retain native part dimensions; target mismatches are reported.",
                               "Door/window size or angle targets are reported, not forced by scaling LEGO parts.",
                               "Assembly height and complete exterior bounds require native geometry verification."])
    return report
