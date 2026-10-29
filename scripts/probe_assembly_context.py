"""Measure real lifecycle prompts; these held-out fixtures are never training data."""
import argparse
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
import sys

from car2lego3d.assembly_agent import AssemblyAgent, wire_json
from car2lego3d.assembly_benchmark import read
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training/assembly_model"))
from loop_data import LifecycleDemonstrator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory")
    mission = read(args.suite)["tasks"][0]["mission"]
    tools = AssemblyTools(args.output / "source", args.native_root)
    backend = LifecycleDemonstrator(mission["semantic"]["operations"])
    agent = AssemblyAgent(tools, backend, mission, max_steps=64)
    state = agent.run()
    cases = []

    def capture(name, payload):
        content = wire_json(payload)
        cases.append(dict(id=name, messages=[dict(role="user", content=content)],
            input_sha256=sha256(content.encode()).hexdigest(), fixture_only=True))

    for path in sorted((args.output / "source/inputs").glob("*.json")):
        capture(path.stem, read(path))
    history = deque([json.loads(row) for row in (args.output / "source/agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()], maxlen=8)
    tools.context_resources["skill"] = "车门车窗灯组连接检查" * 100
    observation = tools.call("context", dict(resource="skill", limit=400))
    tools.context_resources["observations"]["probe"] = observation
    history.append(dict(decision=dict(tool="context"), observation=observation))
    write_json(agent.note_path, dict(text=("车门玻璃与门框已经安装检查铰链" * 20)[:128]))
    capture("memory_chinese_page_and_note", agent.context(history, 32))
    (args.output / "cases.jsonl").write_text("".join(wire_json(case)+"\n" for case in cases), encoding="utf-8")
    print(wire_json(dict(cases=len(cases), state=state["stop_reason"], fixture_only=True)))


if __name__ == "__main__":
    main()
