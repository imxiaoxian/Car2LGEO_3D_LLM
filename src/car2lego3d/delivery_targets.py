"""User-selected delivery targets; source IOs are baselines, never generated output."""
from pathlib import Path

from .common import digest


TARGETS = {
    "countach": dict(brand="Lamborghini", model="Countach 5000 Quattrovalvole", set_num="10337-1",
        source_sha256="e40e261946bc21e9fa4cf97edb916f0e8b40d19bd83f519afb3d93b02b071946",
        body_colors=[15], features=["wedge_profile", "scissor_doors", "door_glass_shared_motion", "cabin_end_connections"]),
    "f40": dict(brand="Ferrari", model="F40", set_num="10248-1",
        source_sha256="66d988f47dde45bde999ae7088d8393c1574c12c73e882ae9bece657ca000b9b",
        body_colors=[4], features=["rear_wing", "side_intakes", "pop_up_headlights", "rear_clamshell"]),
    "nissan-r34": dict(brand="Nissan", model="Skyline GT-R (R34)", set_num="42210-1",
        source_sha256="8e3103d15ee047dd1864612480e912b3333389a6f1160e3ce764b2989b9e545b",
        body_colors=[71, 1], features=["2_fast_2_furious_livery", "four_round_tail_lights", "two_door_cabin", "seated_wheel_groups"]),
    "bugatti-chiron-pur-sport": dict(brand="Bugatti", model="Chiron Pur Sport", set_num="42222-1",
        source_sha256="17322663902f0b673751ef908c2a42e565fb30b4a3adb86f4c7332973aa47fff",
        body_colors=[1, 0], features=["c_shaped_side", "front_lamp_array", "large_rear_wing", "continuous_body_surfaces"]),
}


def freeze_sources(source_root):
    from .assembly_graph import IOArchive
    from .baseline_detail import detail_standard, baseline_observations
    results = {}
    for case, target in TARGETS.items():
        sources = [p for p in Path(source_root).glob("*.io") if "[" + target["set_num"] + "]" in p.name]
        if len(sources) != 1 or digest(sources[0]) != target["source_sha256"]:
            raise ValueError("Delivery source is missing, ambiguous or changed: " + case)
        source = sources[0]
        standard = detail_standard(target)
        results[case] = dict(target, source=str(source.resolve()), source_role="baseline_and_acceptance_only",
            source_bom=IOArchive(source).assembly.bom(), year=None, year_required=False,
            dimensions=dict(mode="auto", nominal_scale=12, enforce=False),
            wheels="match_baseline_io", interior="match_baseline_io_detail_level",
            detail_standard=standard, baseline_observations=baseline_observations(case),
            mechanisms="match_verified_baseline_io_mechanisms",
            acceptance="unverified", reference_identity="requires_reference_gate")
    return dict(schema="vehicle-delivery-targets.v1", order=list(TARGETS), targets=results,
        fresh_creation=False, acceptance="unverified")


def training_audit(corpus, datasets, *, targets=None):
    """Report contamination and protocol drift without editing datasets or adapters."""
    from .assembly_website import vehicle_exclusions
    from .assembly_protocol import protocol_hash
    exclusions = dict(models=set(), families=set(), hashes=set())
    per_case = {}
    for case, target in (targets or TARGETS).items():
        held_out = vehicle_exclusions(corpus, target)
        per_case[case] = held_out
        for key in exclusions:
            exclusions[key].update(held_out[key])
    results = []
    for filename in datasets:
        filename = Path(filename)
        conflicts, unknown, stale, count = [], [], [], 0
        with filename.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                count += 1
                import json
                row = json.loads(line)
                source = row.get("source", {})
                model, families, hashes = source.get("model_id"), set(source.get("families", [])), set(source.get("module_hashes", []))
                if source.get("family"):
                    families.add(source["family"])
                reasons = []
                if model in exclusions["models"]:
                    reasons.append("target_model")
                if families & exclusions["families"]:
                    reasons.append("target_family")
                if hashes & exclusions["hashes"]:
                    reasons.append("target_duplicate_module")
                if reasons:
                    conflicts.append(dict(line=number, model=model, reasons=reasons))
                if not model or not families or not hashes:
                    unknown.append(number)
                if source.get("protocol_hash") != protocol_hash():
                    stale.append(number)
        results.append(dict(file=str(filename.resolve()), sha256=digest(filename), samples=count,
            conflicts=conflicts, unknown_provenance=unknown, stale_protocol=stale,
            eligible_for_current_comparison=not conflicts and not unknown and not stale))
    return dict(schema="vehicle-training-isolation-audit.v1", corpus_snapshot=corpus.snapshot(),
        protocol_hash=protocol_hash(), exclusions={k: sorted(v) for k, v in exclusions.items()},
        per_case=per_case, datasets=results,
        current_comparison_eligible=bool(results) and all(r["eligible_for_current_comparison"] for r in results),
        action="audit_only_no_training_or_default_change")
