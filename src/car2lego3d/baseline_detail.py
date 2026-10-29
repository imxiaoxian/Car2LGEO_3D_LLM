"""User-approved detail budget, distinct from assembly correctness evidence."""
from copy import deepcopy


VERSION = "vehicle-detail-standard.v1"

INSTRUCTIONS = """Match the appearance and structural DETAIL LEVEL of the supplied knowledge-base IO
standard. A reference photograph describes shape; it does not make every real-car component mandatory.
Preserve the recognizable silhouette, cabin, wheel fit, lamp/window outlines and baseline mechanisms.
Do not add hidden pedal hardware, seat rails, optical reflectors, latches or suspension merely because
the real car has them. Lamp layers and separate transparent door panes are required only when the
baseline scope calls for them. An open window/frame representation can be correct for a LEGO model.
Any glass that IS included must fit its opening and move with its own door. All included parts still
need valid native dimensions, mounting, clearance and dependencies. Keep useful existing geometry.
Do not erase features represented by the baseline or use an entire car-shell mesh as an assembly.
Source IOs define the acceptance budget, not fresh generated output or held-out retrieval candidates.
"""

# These observations describe the exact source archives, not the real cars. In
# particular, anonymous submodel groups cannot establish an opening mechanism.
OBSERVATIONS = {
    "countach": dict(parts=1505, spring_parts=[],
        door_sections=["sportello dx", "sportello sx"], door_transparent_parts=0,
        cover_sections=["cofano portabagagli", "cofano motore"],
        notes="Separate door/cover and hinge submodels; no spring/shock parts in source BOM."),
    "f40": dict(parts=1157, spring_parts=[],
        door_sections=["10248 - door left.ldr", "10248 - door right.ldr"], door_transparent_parts=0,
        cover_sections=["10248 - front.ldr", "10248 - engine hood.ldr"],
        notes="Separate doors, headlight pods and engine hood; steering wheel alone is not working steering."),
    "nissan-r34": dict(parts=1401, spring_parts=[dict(part="41838.dat", count=4)],
        door_sections=[], cover_sections=[],
        notes="Four shock absorbers and steering rack; generic group names leave cover articulation unverified."),
    "bugatti-chiron-pur-sport": dict(parts=790, spring_parts=[],
        door_sections=[], cover_sections=[],
        notes="Steering rack, no spring/shock parts; generic group names leave cover articulation unverified."),
}


def detail_standard(request):
    """A small acceptance profile; never exposes held-out source parts to planners."""
    from .delivery_targets import TARGETS
    from .vehicle_delivery import core_case
    brand, model = request.get("brand") or "", (request.get("model") or "").strip()
    case = core_case(brand, model)
    if case not in TARGETS and not brand:
        # Physical specification snapshots use one combined model string.
        combined_brand, _, combined_model = model.partition(" ")
        case = core_case(combined_brand, combined_model.strip())
        if case not in TARGETS:
            from .official_library import identity_pattern
            for candidate, target in TARGETS.items():
                prefixes = [target["model"].split()[0]]
                if candidate == "nissan-r34":
                    prefixes.append("R34")
                # Infer a missing brand only from a model prefix, never from a
                # token embedded in an explicitly different brand/model string.
                if (any(identity_pattern(prefix).match(model.casefold()) for prefix in prefixes)
                        and core_case(target["brand"], model) == candidate):
                    case = candidate
                    break
    target = TARGETS.get(case)
    if not target:
        return None
    return dict(schema=VERSION, basis="knowledge_base_io", case=case,
        source=dict(set_num=target["set_num"], sha256=target["source_sha256"]),
        scope="match_baseline_appearance_and_structure_not_full_real_car",
        nominal_scale=12, scale_enforced=False,
        instructions=INSTRUCTIONS,
        suspension_required=case == "nissan-r34",
        steering_required=case != "f40",
        separate_door_glass_required=False,
        layered_optics_required=False,
        cover_scope="required" if case in {"countach", "f40"} else "baseline_review_required",
        not_required=["hidden_pedal_hardware", "seat_mount_microdetail", "separate_optical_reflector",
            "real_car_rim_hole_count", "real_car_only_mechanisms"],
        quality_checks=["native_part_dimensions", "connectivity", "clearance", "dependency_integrity",
            "included_glass_ownership", "baseline_feature_coverage"],
        acceptance="unverified")


def baseline_observations(case):
    return deepcopy(OBSERVATIONS[case])


# Exact findings reviewed against the user's revised standard. Other rim defects
# (missing tyres, detached rims, wrong fit) must not be dismissed by defect kind.
COUNTACH_RIM_SCOPE = {
    "032dafe5d45459c7aeb2573e": "82abd0441618b31679492fcea02f033c6b54aee5a676855c18207a88c5b55dbe",
    "f71e60987c279c7a3679e1d9": "500b9b2c88bad43e17aeb78f94c5e58f491d479ba090bfd44a0b034a8b9301eb",
    "177f762a8c2aea176b241338": "71fb08fb39c7aab2d977642984a8d773476d4922a2866535a873bb1c835dd2e9",
}


def scope_repairs(repairs, standard):
    required, excluded = [], []
    for original in repairs:
        task = deepcopy(original)
        if (standard and standard.get("case") == "countach"
                and task.get("kind") == "rim_geometry"
                and task.get("id") in COUNTACH_RIM_SCOPE
                and COUNTACH_RIM_SCOPE[task["id"]] == task.get("source_sha256")):
            excluded.append(dict(task=task, status="not_required_by_baseline",
                reason="The source IO uses LEGO rims; exactly five real-car openings is outside the agreed detail scope.",
                standard_source=standard["source"]))
        else:
            required.append(task)
    return required, excluded
