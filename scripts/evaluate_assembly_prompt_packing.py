"""Compare raw/packed Codex decisions on an immutable, replayed module checkpoint."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, parse_decision, wire_json
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_prompt_codec import content_hash, pack_payload, unpack_payload
from car2lego3d.assembly_requirements import descendants
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


MUTATIONS = {"edit", "graft", "snap", "attach", "build", "bind_module", "restore_candidate"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def replay(directory, initial, mission, records, native, corpus, exclusions):
    # Packages need a separate dependency-copy audit before this probe supports them.
    if initial.get("packages") or initial.get("dependencies"):
        raise ValueError("Probe currently requires a native-only initial checkpoint")
    state = {key: deepcopy(initial[key]) for key in ("text", "revision", "joints", "source_sha256")}
    bindings = deepcopy(mission.get("module_task", {}).get("prerequisite_bindings", {}))
    state.update(module_bindings=bindings, packages=[], source_archive=None)
    write_json(directory / "state.json", state)
    tools = AssemblyTools(directory, native, corpus, exclude_models=exclusions["models"],
        exclude_families=exclusions["families"], exclude_hashes=exclusions["hashes"])
    tools.vehicle_contract = mission.get("vehicle_contract")
    graph = tools.assembly.graph()
    members = set().union(*(descendants(graph, item["roots"]) for item in bindings.values()))
    tools.protected_state = dict(bindings=deepcopy(bindings),
        instances={node["id"]: deepcopy(node) for node in graph["instances"] if node["id"] in members},
        joints={key: deepcopy(value) for key, value in graph["joints"].items() if key in members})
    for record in records:
        tool, arguments = parse_decision(record["decision"])
        if tool not in MUTATIONS:
            continue
        result = tools.call(tool, arguments)
        expected = record["observation"]
        if (result["ok"] != expected["ok"]
                or result["revision_after"] != expected.get("revision_after")):
            raise ValueError(f"Checkpoint replay diverged at step {record['step']}")
    return tools


def usage(directory):
    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("type") == "turn.completed":
            return item.get("usage", {})
    return {}


def evaluate(agent, attempt, step, output, *, model=None, timeout=600):
    agent, attempt, output = map(lambda p: Path(p).resolve(), (agent, attempt, output))
    if output.exists() or output.is_relative_to(agent) or agent.is_relative_to(output):
        raise ValueError("Use a new output directory outside the source assembly")
    if not attempt.is_relative_to(agent) or type(step) is not int or step < 0:
        raise ValueError("Use a nonnegative step inside the source assembly")
    input_path = attempt / "inputs" / f"{step:04d}.json"
    originals = [input_path, attempt / "initial-assembly.json", attempt / "agent.json", agent / "launch.json"]
    hashes = {str(path): digest(path) for path in originals}
    payload, initial, recorded, launch = map(read, originals)
    records = [item for line in (attempt / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()
               if (item := json.loads(line))["step"] < step]
    if sorted(row["step"] for row in records) != list(range(step)):
        raise ValueError("Replay requires every prior decision")
    packed = pack_payload(payload)
    if content_hash(unpack_payload(packed)) != content_hash(payload):
        raise ValueError("Packed input did not round-trip exactly")
    args, contract = launch["arguments"], recorded["contract"]
    mission = contract["mission"]
    corpus = Corpus(args["corpus_root"], snapshot=args["snapshot"])
    report = dict(schema="assembly-prompt-packing-evaluation.v1", step=step,
        source_hashes=hashes, frozen_input_sha256=content_hash(payload),
        snapshot=args["snapshot"], exclusions=contract["exclusions"], exact_roundtrip=True,
        variants=[], model_requests=0, production_enabled=False, vehicle_acceptance=False,
        limits="One-decision transport experiment, not vehicle or planner quality acceptance.")
    try:
        write_json(output / "frozen-records.json", records)
        write_json(output / "initial-assembly.json", initial)
        write_json(output / "mission.json", mission)
        write_json(output / "raw-input.json", payload)
        write_json(output / "packed-input.json", packed)
        tools_by_name = {}
        for name in ("raw", "packed"):
            tools = replay(output / name / "assembly", initial, mission, records,
                args["native_root"], corpus, contract["exclusions"])
            graph = tools.assembly.graph()
            expected = payload["graph_summary"]
            if (graph["revision"] != payload["state"]["revision"]
                    or graph["instances"][-100:] != expected["instances"]
                    or tools.assembly.bom() != expected["bom"]):
                raise ValueError("Replayed checkpoint differs from archived planner input")
            # Populate the same addressable resources without invoking a planner.
            AssemblyAgent(tools, CodexPlanner(output / name / "unused"), mission,
                directory=output / name / "context", finish_check=lambda: None, finalize=False)
            tools.context_resources["observations"] = {str(r["step"]): r["observation"] for r in records}
            for record in reversed(records):
                if record["decision"]["tool"] != "context":
                    tools.context_resources["observation"] = record["observation"]
                    break
            tools_by_name[name] = tools
        for name, request in (("raw", payload), ("packed", packed)):
            directory = output / name
            backend = CodexPlanner(directory / "planner", model=model, timeout=timeout)
            started = time.monotonic()
            item = dict(name=name, wire_bytes=len(wire_json(request).encode()),
                checkpoint_sha256=content_hash(tools_by_name[name].assembly.graph()))
            report["model_requests"] += 1
            try:
                decision = backend.propose(request, 0)
                item.update(decision=decision, usage=usage(directory / "planner/0000"))
                tool, arguments = parse_decision(decision)
                if tool == "finish":
                    item["execution"] = dict(ok=False, error="An isolated decision cannot certify module completion")
                else:
                    item["execution"] = tools_by_name[name].call(tool, arguments)
                item["check_after"] = tools_by_name[name].call("check", {})
                item["graph_after_sha256"] = content_hash(tools_by_name[name].assembly.graph())
            except Exception as error:
                item["error"] = dict(type=type(error).__name__, message=str(error))
            item["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["variants"].append(item)
            write_json(output / "report.json", report)
        raw, compact = report["variants"]
        report["same_action"] = all("decision" in item for item in (raw, compact)) and (
            parse_decision(raw["decision"]) == parse_decision(compact["decision"]))
        report["same_resulting_graph"] = all("graph_after_sha256" in item for item in (raw, compact)) and (
            raw["graph_after_sha256"] == compact["graph_after_sha256"])
        report["source_unchanged"] = all(digest(Path(path)) == value for path, value in hashes.items())
        if not report["source_unchanged"]:
            raise ValueError("Archived source evidence changed during experiment")
        write_json(output / "report.json", report)
        return report
    finally:
        corpus.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    args = parser.parse_args()
    report = evaluate(**vars(args))
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"variants", "source_hashes", "exclusions"}}))
    for item in report["variants"]:
        print(json.dumps({key: value for key, value in item.items()
                          if key not in {"execution", "check_after"}}))


if __name__ == "__main__":
    main()
