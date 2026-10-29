"""Replay a stopped module's context without model calls or source mutations."""
import argparse
from collections import deque
import json
from pathlib import Path

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, wire_json
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def read(path):
    return path.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--module", help="Explicit module when the stopped schedule has no active task")
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory")
    schedule_path = args.agent / "module-tasks.json"
    schedule = json.loads(read(schedule_path))
    module = args.module or schedule["active"]
    if module not in schedule["tasks"] or not schedule["tasks"][module].get("attempts"):
        parser.error("Select a module with an existing attempt using --module")
    task = schedule["tasks"][module]
    attempt = args.agent / task["attempts"][-1]["directory"]
    vehicle_path = args.agent / "context-resources/vehicle-context.json"
    note_path = attempt / "planner-note.json"
    paths = [args.agent / "state.json", schedule_path, attempt / "agent.json",
             attempt / "agent-decisions.jsonl", vehicle_path]
    if note_path.is_file():
        paths.append(note_path)
    source_hashes = {str(path): digest(path) for path in paths}
    previous = json.loads(read(sorted((attempt / "inputs").glob("*.json"))[-1]))
    mission = json.loads(read(attempt / "agent.json"))["contract"]["mission"]
    write_json(args.output / "state.json", json.loads(read(paths[0])))
    tools = AssemblyTools(args.output, args.native_root)
    backend = CodexPlanner(args.output / "planner")
    backend.vehicle_context = json.loads(read(vehicle_path))
    agent = AssemblyAgent(tools, backend, mission, finish_check=lambda: None, finalize=False)
    if note_path.is_file():
        write_json(agent.note_path, json.loads(read(note_path)))
    history = deque(maxlen=12)
    for line in read(attempt / "agent-decisions.jsonl").splitlines():
        record = json.loads(line)
        history.append(record)
        tools.context_resources["observations"][str(record["step"])] = record["observation"]
    payload = agent.context(history, previous["remaining_steps"])
    if payload["state"] != previous["state"]:
        raise ValueError("Stopped graph differs from the comparison input")
    if payload["note"] != previous["note"]:
        raise ValueError("Stopped planner note differs from the comparison input")
    write_json(args.output / "payload.json", payload)
    size = lambda value: len(wire_json(value).encode("utf-8"))
    checks = {}
    for name, pointer in (("vehicle_context", "/specifications"), ("module_task", "/requirement"),
                          ("protocol", ""), ("skill", ""), ("observations", "/0")):
        result = tools.call("context", dict(resource=name, pointer=pointer))
        checks[name] = dict(ok=result["ok"], pointer=pointer)
    if not all(item["ok"] for item in checks.values()):
        raise ValueError("Context resources are not reachable")
    if any(digest(Path(path)) != expected for path, expected in source_hashes.items()):
        raise ValueError("Source checkpoint changed during probe")
    report = dict(schema="assembly-context-comparison.v1", read_only_source=True,
        model_calls=0, before_bytes=size(previous), after_bytes=size(payload),
        reduction_percent=round(100 * (1 - size(payload) / size(previous)), 2),
        before_fields={key: size(value) for key, value in previous.items()},
        after_fields={key: size(value) for key, value in payload.items()},
        reachable=checks, source_hashes=source_hashes,
        limits="Context transport measurement only; no planner or vehicle quality acceptance.")
    write_json(args.output / "report.json", report)
    print(wire_json(report))


if __name__ == "__main__":
    main()
