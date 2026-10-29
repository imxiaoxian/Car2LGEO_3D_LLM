"""Lifecycle planning fixtures are protocol tests, never training data."""
import json
from pathlib import Path
import tempfile
import unittest

from loop_data import (LifecycleDemonstrator, CONTEXT_VERSION, execution_hash, ordered_sources,
    protocol_hash, reusable_demonstration, skill_bundle, unused_attempt, verify_contract,
    relocate_evidence_paths)


class Lifecycle(unittest.TestCase):
    def test_relocation_changes_only_known_local_evidence_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay, source = root / "replay", root / "source"
            outside = str(root / "external.json")
            observation = dict(result=dict(module_progress=dict(evidence_files=[
                dict(file=str(replay / "checks.json"), sha256="expected"),
                dict(file=outside, sha256="external")]), other=str(replay / "untouched")))
            relocated = relocate_evidence_paths(observation, replay, source)
            files = relocated["result"]["module_progress"]["evidence_files"]
            self.assertEqual(files[0], dict(file=str(source / "checks.json"), sha256="expected"))
            self.assertEqual(files[1]["file"], outside)
            self.assertEqual(relocated["result"]["other"], observation["result"]["other"])
            self.assertEqual(observation["result"]["module_progress"]["evidence_files"][0]["file"], str(replay / "checks.json"))

    def test_retry_paths_preserve_existing_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "source"
            self.assertEqual(unused_attempt(target), target)
            target.mkdir()
            self.assertEqual(unused_attempt(target), target)
            (target / "partial.json").write_text("preserved", encoding="utf-8")
            retry = unused_attempt(target)
            self.assertEqual(retry.name, "source-retry-1")
            retry.mkdir()
            self.assertEqual(unused_attempt(target).name, "source-retry-2")
            self.assertEqual((target / "partial.json").read_text(encoding="utf-8"), "preserved")

    def test_defer_slow_preserves_indices_and_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            provenance = {}
            for name, seconds, split in (("a", 300, "train"), ("b", 1, "test"), ("c", 2, "validation")):
                root = Path(temporary) / name
                root.mkdir()
                (root / "agent.json").write_text(json.dumps(dict(elapsed_seconds=seconds)), encoding="utf-8")
                provenance[str(root)] = (split, dict(model_id=name))
            ordered = ordered_sources(provenance, True)
            self.assertEqual([row[0] for row in ordered], [1, 2, 0])
            self.assertEqual([row[1][1][0] for row in ordered], ["test", "validation", "train"])
            self.assertEqual([row[0] for row in ordered_sources(provenance, False)], [0, 1, 2])

    def test_resume_requires_finished_identical_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mission = dict(request=dict(goal="fixture only"))
            self.assertIsNone(reusable_demonstration(root, mission, "fixture"))
            contract = dict(execution_hash=execution_hash(), protocol_hash=protocol_hash(), snapshot="fixture",
                mission=mission, skill_hash=skill_bundle()["sha256"], context_version=CONTEXT_VERSION)
            state = dict(stop_reason="planner_finished", digital_status="pass", contract=contract)
            path = root / "agent.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(reusable_demonstration(root, mission, "fixture"), state)
            state["stop_reason"] = "tool_budget_exhausted"
            path.write_text(json.dumps(state), encoding="utf-8")
            self.assertIsNone(reusable_demonstration(root, mission, "fixture"))
            state["stop_reason"] = "planner_finished"
            contract["execution_hash"] = "changed"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different frozen contract"):
                reusable_demonstration(root, mission, "fixture")

    def test_explicit_runtime_contract_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            value = dict(context_version=CONTEXT_VERSION, execution_hash=execution_hash(),
                protocol_hash=protocol_hash(), skill_hash=skill_bundle()["sha256"], snapshot="fixture")
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(verify_contract(path, "fixture")["context_version"], CONTEXT_VERSION)
            value["skill_hash"] = "changed"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen training contract"):
                verify_contract(path, "fixture")

    def test_complete_pages_are_read_before_next_source_action(self):
        operation = dict(op="add", part="3001.dat", color=4,
            matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        planner = LifecycleDemonstrator([operation])
        first = planner.propose({}, 0)
        self.assertEqual(json.loads(first["arguments_json"]),
                         dict(resource="guide", pointer="/start", limit=400))
        continued = planner.propose(dict(last=dict(tool="context", data=dict(
            resource="guide", pointer="/start", next_offset=400))), 1)
        self.assertEqual(json.loads(continued["arguments_json"]),
                         dict(resource="guide", pointer="/start", offset=400, limit=400))
        after = planner.propose(dict(last=dict(tool="context", data=dict(next_offset=None))), 2)
        self.assertEqual(json.loads(after["arguments_json"])["pointer"], "/tools/inspect")

    def test_source_actions_preserve_placements_and_close_lifecycle(self):
        operations = [dict(op="add", part="3001.dat", color=4, matrix=[[1, 0, 0, 0],
            [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])]
        planner = LifecycleDemonstrator(operations)
        decisions = [planner.propose({}, step) for step in range(len(planner.actions))]
        names = [row["tool"] for row in decisions]
        self.assertIn("inspect", names)
        self.assertIn("remember", names)
        self.assertLess(names.index("inspect"), names.index("edit"))
        self.assertLess(names.index("remember"), names.index("edit"))
        self.assertEqual(names[-5:], ["context", "check", "context", "export", "finish"])
        edits = [json.loads(row["arguments_json"]) for row in decisions if row["tool"] == "edit"]
        self.assertEqual(edits, [dict(revision=0, operations=operations)])
        self.assertEqual(json.loads(decisions[-1]["arguments_json"]), {})


if __name__ == "__main__":
    unittest.main()
