"""Summarize planner lifecycle failures without changing execution evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path


def diagnose(directory):
    state = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    operations = state.get("contract", {}).get("mission", {}).get("semantic", {}).get("operations", [])
    rows = [json.loads(line) for line in (directory / "agent-decisions.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    tools, pointers, invalid = Counter(), Counter(), []
    lifecycle, errors = [], []
    for row in rows:
        decision = row.get("decision") or {}
        tools[decision.get("tool", "missing")] += 1
        observation = row.get("observation") or {}
        tool = decision.get("tool")
        if tool in {"check", "export", "finish"}:
            lifecycle.append(dict(step=row.get("step"), tool=tool,
                                  ok=observation.get("ok")))
        if observation.get("ok") is False:
            errors.append(dict(step=row.get("step"), tool=tool,
                               error=observation.get("error")))
        arguments = decision.get("arguments_json", "{}")
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (ValueError, TypeError):
            continue
        if not isinstance(arguments, dict):
            continue
        pointer = arguments.get("pointer", "")
        if decision.get("tool") == "context" and arguments.get("resource") == "semantic":
            read_key = json.dumps(dict(pointer=pointer, offset=arguments.get("offset", 0),
                                       limit=arguments.get("limit")), sort_keys=True)
            pointers[read_key] += 1
            if pointer.startswith("/operations/"):
                index = pointer.removeprefix("/operations/").split("/")[0]
                if index.isdigit() and int(index) >= len(operations):
                    invalid.append(dict(step=row.get("step"), pointer=pointer))
    result_file = directory / "benchmark-result.json"
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else None
    return dict(
        directory=str(directory), status=state.get("status"), steps=state.get("steps"),
        stop_reason=state.get("stop_reason"), source_placements=len(operations),
        decision_tools=dict(tools), repeated_semantic_reads={k: v for k, v in pointers.items() if v > 1},
        out_of_range_placement_reads=invalid,
        planner_lifecycle=lifecycle,
        tool_errors=errors,
        lifecycle_scope="Planner-issued actions only; host post-run checks never count as planner check/export/finish.",
        single_placement_demonstration_actions=2 * len(operations) + 3,
        demonstration_budget_scope="Context plus edit per placement, then check/export/finish; excludes navigation overhead. Not a required planner strategy.",
        benchmark_metrics={k: v for k, v in result.get("metrics", {}).items()
                           if k not in {"missing", "extra"}} if result else None,
        scope="Read-only lifecycle diagnosis; not structure, appearance or delivery acceptance.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps([diagnose(path) for path in args.directories], ensure_ascii=False, indent=2))
