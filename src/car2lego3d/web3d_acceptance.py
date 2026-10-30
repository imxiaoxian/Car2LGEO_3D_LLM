"""Acceptance harness: generate models with the real gpt-6-astra model.

Writes each run to an output directory with the assembly, LDraw export and a
measured report, so quality can be compared against the reference models in
``D:/lego/result`` (1127-4122 parts, 207-524 unique part types).

Usage:
    python -m car2lego3d.web3d_acceptance --output runs/web3d-acceptance
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .instruction_expander import describe
from .ldraw_catalogue import load_catalogue
from .llm_instruction_generator import (
    LLMInstructionGenerator, ModelCategory, ModelParameters, ModelStyle,
)
from .web3d_prototype import AssemblyInstructions, LDrawExporter


# Benchmarks measured from the complex car models in D:/lego/result.
BENCHMARK = {
    "min_parts": 800,
    "target_parts": 1500,
    "min_unique_types": 40,
    "min_colors": 4,
}

CASES = [
    dict(name="sports-car", category=ModelCategory.VEHICLE, style=ModelStyle.REALISTIC,
         description="Red mid-engine sports car with a large rear wing, low nose, "
                     "pop-up style headlights and a glass engine cover",
         scale="1:24", max_parts=2200,
         colors=["Bright Red", "Black", "White", "Trans-Clear", "Dark Bluish Gray"]),
    dict(name="pickup-truck", category=ModelCategory.VEHICLE, style=ModelStyle.REALISTIC,
         description="Blue crew-cab pickup truck with an open cargo bed, roof rack "
                     "and chunky off-road tyres",
         scale="1:24", max_parts=1800,
         colors=["Bright Blue", "Black", "Dark Bluish Gray", "Trans-Clear", "Flat Silver"]),
    dict(name="town-house", category=ModelCategory.BUILDING, style=ModelStyle.STYLIZED,
         description="Two-storey corner town house with a red gabled roof, white walls "
                     "and shop windows on the ground floor",
         scale="minifig", max_parts=3000,
         colors=["White", "Bright Red", "Tan", "Dark Tan", "Trans-Clear", "Brown"]),
]


def evaluate(parts, params) -> dict:
    """Score one generated model against the acceptance criteria."""
    summary = describe(parts)
    unique = {p.brick_definition.lego_id for p in parts}
    colors = {p.color for p in parts}
    checks = {
        "has_parts": summary["parts"] > 0,
        "within_budget": summary["parts"] <= params.max_parts,
        "reaches_benchmark_scale": summary["parts"] >= BENCHMARK["min_parts"],
        "enough_part_types": len(unique) >= BENCHMARK["min_unique_types"],
        "enough_colors": len(colors) >= BENCHMARK["min_colors"],
    }
    return {
        "summary": summary,
        "checks": checks,
        "passed": all(checks.values()),
        "unique_types": len(unique),
        "colors_used": sorted(colors),
    }


def run_case(case: dict, generator: LLMInstructionGenerator, output: Path,
             reuse: bool = False) -> dict:
    """Generate, expand, validate and export one model."""
    name = case["name"]
    case_dir = output / name
    case_dir.mkdir(parents=True, exist_ok=True)

    params = ModelParameters(
        category=case["category"],
        description=case["description"],
        style=case["style"],
        target_scale=case["scale"],
        max_parts=case["max_parts"],
        color_scheme=case["colors"],
    )

    started = time.time()
    result = generator.generate_instructions(params)
    elapsed = time.time() - started

    record = {
        "case": name,
        "description": case["description"],
        "seconds": round(elapsed, 2),
        "success": result.success,
        "tokens": result.tokens_used,
        "error": result.error,
    }
    if not result.success or result.assembly is None:
        (case_dir / "report.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        return record

    parts = result.assembly.parts
    evaluation = evaluate(parts, params)
    validation = result.assembly.validate()

    record.update({
        "evaluation": evaluation,
        "validation": {
            "is_valid": validation.is_valid,
            "structural_score": round(validation.structural_score, 3),
            "issues": len(validation.issues),
            "errors": [i.message for i in validation.issues if i.severity == "error"][:5],
        },
        "steps": len(result.instructions.steps),
        "warnings": result.instructions.warnings,
        "reasoning": result.reasoning[:1000],
        "passed": evaluation["passed"] and validation.is_valid,
    })

    result.assembly.save_to_file(case_dir / "assembly.json")
    (case_dir / "model.ldr").write_text(
        LDrawExporter.export(result.assembly), encoding="utf-8")
    (case_dir / "report.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Web 3D LEGO acceptance run")
    parser.add_argument("--output", default="runs/web3d-acceptance")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--case", action="append", dest="cases",
                        help="Only run these case names (repeatable)")
    args = parser.parse_args(argv)

    catalogue = load_catalogue()
    if len(catalogue) == 0:
        print("No LDraw library found; set CAR2LEGO_LDRAW_ROOTS.")
        return 2
    print(f"LDraw parts available: {len(catalogue)}")

    generator = LLMInstructionGenerator(model=args.model, catalogue=catalogue)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    selected = [c for c in CASES if not args.cases or c["name"] in args.cases]
    records = []
    for case in selected:
        print(f"\n=== {case['name']} ===")
        record = run_case(case, generator, output)
        records.append(record)
        if record["success"]:
            ev = record["evaluation"]
            print(f"  parts:            {ev['summary']['parts']}")
            print(f"  unique types:     {ev['unique_types']}")
            print(f"  colours:          {len(ev['colors_used'])}")
            print(f"  extent (studs):   {ev['summary']['extent_studs']}")
            print(f"  structural score: {record['validation']['structural_score']}")
            print(f"  steps:            {record['steps']}")
            print(f"  time:             {record['seconds']}s  tokens: {record['tokens']}")
            failed = [k for k, v in ev["checks"].items() if not v]
            print(f"  PASSED" if record["passed"] else f"  FAILED checks: {failed}")
        else:
            print(f"  FAILED: {record['error']}")

    summary_path = output / "acceptance.json"
    summary_path.write_text(
        json.dumps({"benchmark": BENCHMARK, "cases": records}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    passed = sum(1 for r in records if r.get("passed"))
    print(f"\n{passed}/{len(records)} cases passed. Report: {summary_path}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
