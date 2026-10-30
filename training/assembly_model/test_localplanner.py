"""Website-to-service HTTP contract tests; responses are fixtures, not model evidence."""
from http.server import ThreadingHTTPServer
from hashlib import sha256
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from car2lego3d.assembly_agent import DECISION_TOKENS, LocalPlanner, wire_json
from car2lego3d.assembly_protocol import DECISION
from serve import RequestError, Runtime, handler_for, validate_request


class WebsiteContract(unittest.TestCase):
    def test_actual_localplanner_sends_unchanged_payload_and_parses_response(self):
        class FixtureRuntime(Runtime):
            received = None

            def infer(self, body):
                messages, schema, budget = validate_request(body, self.name)
                self.received = (messages, schema, budget)
                decision = dict(tool="inspect", arguments_json="{}", reason="contract fixture")
                return dict(model=self.name, choices=[dict(finish_reason="stop", message=dict(role="assistant", content=json.dumps(decision)))])

        runtime = FixtureRuntime()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            planner = LocalPlanner("local_base", url=f"http://127.0.0.1:{server.server_port}")
            payload = dict(mission={"vehicle": "Countach"}, skill="complete fixture context", state=dict(revision=0))
            result = planner.propose(payload, 0)
            self.assertEqual(result["tool"], "inspect")
            self.assertEqual(runtime.received[0], [dict(role="user", content=wire_json(payload))])
            self.assertEqual(runtime.received[1], DECISION)
            self.assertEqual(runtime.received[2], DECISION_TOKENS)
            self.assertEqual(DECISION_TOKENS, 256)
            with tempfile.TemporaryDirectory() as tmp:
                cases, output = Path(tmp) / "held-out.jsonl", Path(tmp) / "replay.json"
                message = wire_json(payload)
                case = dict(id="contract-only", messages=[dict(role="user", content=message)],
                    input_sha256=sha256(message.encode()).hexdigest(), decision_schema=DECISION)
                cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
                run = subprocess.run([sys.executable, str(Path(__file__).parent / "evaluate.py"),
                    "--cases", str(cases), "--output", str(output), "--url", planner.url,
                    "--model", "local_base"], capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                replay = json.loads(output.read_text(encoding="utf-8"))
                self.assertTrue(replay["identity_unchanged"])
                self.assertTrue(replay["results"][0]["proposal_returned"])
                self.assertEqual(replay["service_identity"]["model"], "local_base")
                self.assertEqual(replay["max_tokens"], DECISION_TOKENS)
                self.assertEqual(runtime.received[2], DECISION_TOKENS)
                run = subprocess.run([sys.executable, str(Path(__file__).parent / "evaluate.py"),
                    "--cases", str(cases), "--output", str(output), "--url", planner.url,
                    "--model", "local_base", "--max-tokens", "128"], capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(runtime.received[2], 128)
                replay = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(replay["max_tokens"], 128)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_replay_preserves_explicit_context_failure(self):
        class RejectedRuntime(Runtime):
            def infer(self, body):
                validate_request(body, self.name)
                raise RequestError("Context limit exceeded: input_tokens=800, requested_output_tokens=256, max_context=1024; no silent truncation")

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(RejectedRuntime()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cases, output = Path(tmp) / "held-out.jsonl", Path(tmp) / "replay.json"
                message = '{"fixture":"context-rejection-contract-only"}'
                case = dict(id="context-failure-contract", messages=[dict(role="user", content=message)],
                    input_sha256=sha256(message.encode()).hexdigest(), decision_schema=DECISION)
                cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
                run = subprocess.run([sys.executable, str(Path(__file__).parent / "evaluate.py"),
                    "--cases", str(cases), "--output", str(output), "--url", f"http://127.0.0.1:{server.server_port}",
                    "--model", "local_base"], capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                replay = json.loads(output.read_text(encoding="utf-8"))
                row = replay["results"][0]
                self.assertFalse(row["proposal_returned"])
                self.assertEqual(row["http_status"], 400)
                self.assertIn("input_tokens=800", row["error"]["error"]["message"])
                self.assertIn("no silent truncation", row["error"]["error"]["message"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
