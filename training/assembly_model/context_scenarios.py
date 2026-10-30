"""Create contract fixtures from the real website context; never training data."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from car2lego3d.assembly_agent import AssemblyAgent, wire_json
from car2lego3d.assembly_tools import AssemblyTools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-chars", type=int, default=400)
    parser.add_argument("--note-chars", type=int, default=128)
    args = parser.parse_args()
    if not 0 <= args.page_chars <= 800 or not 0 <= args.note_chars <= 256:
        parser.error("Scenario page/note lengths exceed the documented contract")
    def repeat(text, length):
        return (text * ((length // len(text)) + 1))[:length]
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        tools = AssemblyTools(Path(directory), Path(directory) / "fixture-catalog")
        mission = dict(request=dict(brand="Lamborghini", model="Countach", year=None, goal="assemble opening doors"),
            semantic={}, specifications={}, references={})
        agent = AssemblyAgent(tools, None, mission)
        def capture(name, history):
            content = wire_json(agent.context(history, 100))
            cases.append(dict(id=name, messages=[dict(role="user", content=content)],
                input_sha256=sha256(content.encode()).hexdigest(), fixture_only=True))
        capture("initial", [])
        ascii_page = repeat("assembly module port position connection ", args.page_chars)
        unicode_page = repeat("门窗随车门共同运动连接端口安装方向碰撞检查", args.page_chars)
        def history_page(page):
            tools.context_resources["skill"] = page + page
            result = AssemblyTools.tool_context(tools, "skill", limit=args.page_chars)
            return [dict(decision=dict(tool="context"), observation=dict(ok=True, result=result))]
        for name, page in (("ascii_page", ascii_page), ("chinese_page", unicode_page)):
            capture(name, history_page(page))
        agent.note_path.write_text(json.dumps(dict(text=repeat("车门玻璃与门框已经安装检查铰链", args.note_chars))), encoding="utf-8")
        capture("chinese_page_and_note", history_page(unicode_page))
        capture("error_and_note", [dict(decision=dict(tool="edit"), observation=dict(ok=False, error=repeat("连接端口不匹配需要查询完整错误上下文", 256)))])
        capture("large_error_and_note", [dict(decision=dict(tool="edit"), observation=dict(ok=False, error=repeat("连接端口不匹配需要查询完整错误上下文", 1600)))])
        tools.context_resources["request"]["goal"] = repeat("车门车窗灯组及前后车身模块连接验收", 400)
        capture("large_task_page_and_note", history_page(unicode_page))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    print(json.dumps(dict(fixture_only=True, cases=len(cases), page_chars=args.page_chars, note_chars=args.note_chars, output=str(args.output))))


if __name__ == "__main__":
    main()
