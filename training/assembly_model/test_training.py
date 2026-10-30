"""Contract tests; these fixtures are never emitted as training data."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen, Request

from common import config, file_hash, load_samples, tokenize_samples, tokenize_sample_rows
from dataset_audit import audit_splits, partition_identity
from gpu_lock import DEFAULT_LOCK, GPULock
from serve import Runtime, RequestError, handler_for, validate_adapter, validate_request
from http.server import ThreadingHTTPServer


class Contracts(unittest.TestCase):
    def test_pinned_config(self):
        value = config()
        self.assertEqual(value["lora_rank"], 16)
        self.assertEqual(value["context_length"], 1024)
        self.assertEqual(value["oom_context_length"], 512)
        self.assertEqual(value["gradient_accumulation_steps"], 16)

    def test_gpu_lock_is_shared_with_website(self):
        from car2lego3d.gpu_lock import GPULock as WebsiteLock
        self.assertIs(GPULock, WebsiteLock)
        self.assertEqual(DEFAULT_LOCK.resolve(), (Path(__file__).resolve().parent / "runtime/gpu.lock").resolve())

    def test_empty_and_unaudited_data_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "data.jsonl"
            target.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No approved"):
                load_samples(target)
            target.write_text(json.dumps(dict(messages=[], source={})), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing provenance"):
                load_samples(target)

    def sample(self, root):
        messages = [dict(role="user", content='{"state": {}}'), dict(role="assistant", content=json.dumps(dict(tool="check", arguments_json="{}", reason="test")))]
        evidence = dict(execution_succeeded=True, structure_passed=True, source_review_approved=True,
            trajectory_sha256="f"*64, input_sha256=sha256(messages[0]["content"].encode()).hexdigest(),
            decision_sha256=sha256(messages[1]["content"].encode()).hexdigest())
        path = root / "evidence.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        row = dict(messages=messages, source=dict(model_id="fixture", family="fixture", module_hashes=["f"*64], snapshot="s"*64,
            protocol_hash="p"*64, trajectory_sha256="f"*64), evidence=dict(path="evidence.json", sha256=file_hash(path)))
        data = root / "data.jsonl"
        data.write_text(json.dumps(row), encoding="utf-8")
        return data, row, path, evidence

    def test_content_bound_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, row, path, evidence = self.sample(Path(tmp))
            self.assertEqual(len(load_samples(data, expected_snapshot="s"*64)), 1)
            evidence["structure_passed"] = False
            path.write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_samples(data)
            row["evidence"]["sha256"] = file_hash(path)
            data.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "structure_passed"):
                load_samples(data)

    def test_escaping_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, row, _, _ = self.sample(Path(tmp))
            row["evidence"]["path"] = "../outside.json"
            data.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escaping"):
                load_samples(data)

    def test_finish_requires_current_revision_verified_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, row, path, evidence = self.sample(Path(tmp))
            decision = dict(tool="finish", arguments_json="{}", reason="fixture only")
            row["messages"][1]["content"] = json.dumps(decision)
            evidence["decision_sha256"] = sha256(row["messages"][1]["content"].encode()).hexdigest()
            def save():
                path.write_text(json.dumps(evidence), encoding="utf-8")
                row["evidence"]["sha256"] = file_hash(path)
                data.write_text(json.dumps(row), encoding="utf-8")
            save()
            with self.assertRaisesRegex(ValueError, "finish lacks verified export"):
                load_samples(data)
            evidence.update(finish_verified=True, completion=dict(revision=3, export_revision=3,
                archive_roundtrip_verified=True, planner_finished=True))
            save()
            self.assertEqual(len(load_samples(data)), 1)
            for key, value in (("revision", 4), ("export_revision", None),
                               ("archive_roundtrip_verified", False), ("planner_finished", False)):
                original = evidence["completion"][key]
                evidence["completion"][key] = value
                save()
                with self.assertRaisesRegex(ValueError, "current-revision archive"):
                    load_samples(data)
                evidence["completion"][key] = original
            decision["arguments_json"] = '{"unexpected":true}'
            row["messages"][1]["content"] = json.dumps(decision)
            evidence["decision_sha256"] = sha256(row["messages"][1]["content"].encode()).hexdigest()
            save()
            with self.assertRaisesRegex(ValueError, "finish lacks verified export"):
                load_samples(data)

    def test_tokenization_masks_prompt_without_truncating(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return [1, 2, 3] if len(messages) == 1 else [1, 2, 3, 4, 5]
        result, skipped = tokenize_samples([dict(messages=[{}, {}])], Tokenizer(), 5)
        self.assertEqual(result[0]["labels"], [-100, -100, -100, 4, 5])
        self.assertEqual(skipped, 0)
        with self.assertRaisesRegex(ValueError, "No complete samples"):
            tokenize_samples([dict(messages=[{}, {}])], Tokenizer(), 4)
        class SelectiveTokenizer:
            def apply_chat_template(self, messages, **kwargs):
                if len(messages) == 1:
                    return [1, 2]
                return [1, 2, 3, 4, 5, 6] if messages[0].get("long") else [1, 2, 3]
        fitting = dict(messages=[{}, {}], source=dict(family="included"))
        excluded = dict(messages=[dict(long=True), {}], source=dict(family="excluded"))
        encoded, skipped, included = tokenize_sample_rows([excluded, fitting], SelectiveTokenizer(), 4)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(encoded), 1)
        self.assertEqual(included, [fitting])

    def test_split_audit_requires_disjoint_family_modules_and_provenance(self):
        import copy
        def row(name):
            return dict(messages=[dict(content=json.dumps(dict(model=name)))], source=dict(
                family=name, families=[name], model_id=name, module_hashes=[name],
                trajectory_sha256=name, protocol_hash="protocol", snapshot="snapshot"))
        train, validation = row("train"), row("validation")
        extra_family = row("primary")
        extra_family["source"]["families"] = ["secondary"]
        self.assertEqual(partition_identity([extra_family])["families"], {"primary", "secondary"})
        report = audit_splits(dict(train=[train], validation=[validation]), expected_snapshot="snapshot")
        self.assertEqual(report["status"], "disjoint")
        self.assertEqual(report["held_out_partitions"], ["validation"])
        self.assertEqual(report["partitions"]["train"]["samples"], 1)
        self.assertEqual(audit_splits(dict(train=[train]), expected_snapshot="snapshot")["status"], "held_out_not_provided")
        mutations = [
            lambda source: source["families"].append("train"),
            lambda source: source["module_hashes"].append("train"),
            lambda source: source.update(model_id="train"),
            lambda source: source.update(trajectory_sha256="train"),
        ]
        for mutate in mutations:
            overlapping = copy.deepcopy(validation)
            mutate(overlapping["source"])
            with self.assertRaisesRegex(ValueError, "split leakage"):
                audit_splits(dict(train=[train], test=[overlapping]), expected_snapshot="snapshot")
        overlapping = copy.deepcopy(validation)
        overlapping["messages"] = train["messages"]
        with self.assertRaisesRegex(ValueError, "split leakage"):
            audit_splits(dict(train=[train], test=[overlapping]), expected_snapshot="snapshot")
        changed = copy.deepcopy(validation)
        changed["source"]["protocol_hash"] = "changed"
        with self.assertRaisesRegex(ValueError, "identical frozen"):
            audit_splits(dict(train=[train], validation=[changed]), expected_snapshot="snapshot")
        with self.assertRaisesRegex(ValueError, "snapshot mismatch"):
            audit_splits(dict(train=[train]), expected_snapshot="wrong")
        with self.assertRaisesRegex(ValueError, "no approved"):
            audit_splits(dict(train=[train], validation=[]), expected_snapshot="snapshot")

    def test_gpu_lock_excludes_other_process_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "gpu.lock"
            script = "from gpu_lock import GPULock; import sys;\nwith GPULock(sys.argv[1],timeout=0.1,poll=0.02): print('acquired')"
            with GPULock(lock, timeout=1):
                blocked = subprocess.run([sys.executable, "-c", script, str(lock)], cwd=Path(__file__).parent, capture_output=True, text=True)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("GPU busy", blocked.stderr)
            free = subprocess.run([sys.executable, "-c", script, str(lock)], cwd=Path(__file__).parent, capture_output=True, text=True)
            self.assertEqual(free.returncode, 0, free.stderr)
            self.assertIn("acquired", free.stdout)

    def test_model_and_schema_validation(self):
        body = dict(model="local_base", temperature=0, max_tokens=10, messages=[dict(role="user", content="{}")],
            response_format=dict(type="json_schema", json_schema=dict(schema=dict(type="object", properties=dict(tool={}, arguments_json={}, reason={})))) )
        self.assertEqual(validate_request(body, "local_base")[2], 10)
        with self.assertRaisesRegex(RequestError, "model"):
            validate_request(body, "local_finetuned")
        body["temperature"] = 0.5
        with self.assertRaisesRegex(RequestError, "deterministic"):
            validate_request(body, "local_base")
        body["temperature"] = 0
        body["messages"] = [None]
        with self.assertRaises(RequestError):
            validate_request(body, "local_base")
        body["messages"] = [dict(role="user", content="{}")]
        body["response_format"] = None
        with self.assertRaises(RequestError):
            validate_request(body, "local_base")

    def test_context_limit_and_identity_are_explicit(self):
        runtime = Runtime(max_context=1024)
        runtime.check_context(1000, 24)
        with self.assertRaisesRegex(RequestError, "input_tokens=1000.*requested_output_tokens=25.*max_context=1024.*no silent truncation"):
            runtime.check_context(1000, 25)
        identity = runtime.identity()
        self.assertEqual(identity["max_context"], 1024)
        self.assertIsNone(identity["adapter"])
        self.assertFalse(identity["quality_accepted"])
        with self.assertRaises(ValueError):
            Runtime(max_context=32769)

    def test_http_health_and_invalid_request_without_gpu(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(Runtime()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(url + "/health") as response:
                health = json.load(response)
                self.assertEqual(health["model"], "local_base")
                self.assertEqual(health["max_context"], 1024)
                self.assertFalse(health["quality_accepted"])
            request = Request(url + "/v1/chat/completions", data=b"{}", headers={"Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as result:
                urlopen(request)
            self.assertEqual(result.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_adapter_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter"
            adapter.mkdir()
            weight = adapter / "adapter_model.safetensors"
            weight.write_bytes(b"test-only")
            from common import MODEL_ID, REVISION
            adapter_config = adapter / "adapter_config.json"
            adapter_config.write_text(json.dumps(dict(peft_type="LORA", base_model_name_or_path=MODEL_ID)), encoding="utf-8")
            manifest = dict(model_id=MODEL_ID, revision=REVISION, status="trained_not_accepted", samples=1,
                optimizer_steps=1, actual_epochs=1.0,
                adapter_files={weight.name: file_hash(weight), adapter_config.name: file_hash(adapter_config)})
            manifest_path = root / "training-manifest.json"
            for invalid_steps in (None, 0, True, "1"):
                manifest["optimizer_steps"] = invalid_steps
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "optimizer step"):
                    validate_adapter(adapter)
            manifest["optimizer_steps"] = 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            validate_adapter(adapter)
            runtime = Runtime(adapter=adapter)
            identity = runtime.identity()
            self.assertEqual(identity["model"], "local_finetuned")
            self.assertEqual(identity["adapter"]["training_samples"], 1)
            self.assertEqual(identity["adapter"]["optimizer_steps"], 1)
            self.assertEqual(identity["adapter"]["actual_epochs"], 1.0)
            self.assertEqual(identity["adapter"]["manifest_sha256"], file_hash(root / "training-manifest.json"))
            weight.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "integrity"):
                runtime.check_adapter_identity()
            weight.write_bytes(b"test-only")
            (adapter / "unexpected.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory"):
                validate_adapter(adapter)


if __name__ == "__main__":
    unittest.main()
