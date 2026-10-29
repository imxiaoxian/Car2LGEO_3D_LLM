"""Regression gates for complete-epoch release provenance; fixtures are not training data."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, MODEL_ID, REVISION, config, file_hash, write_json
from release_readiness import inspect_release


class ReleaseContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.run = self.root / "run"
        self.run.mkdir()
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        for name in ("train", "validation", "test"):
            (self.dataset / (name + ".jsonl")).write_text(name, encoding="utf-8")
        write_json(self.dataset / "report.json", dict(fixture_only=True))
        cfg = config()
        self.contract = self.root / "contract.json"
        write_json(self.contract, dict(implementation={}, execution_hash="e" * 64,
            protocol_hash=cfg["protocol_hash"], skill_hash="s" * 64,
            context_version="assembly-context.v3", snapshot=cfg["snapshot"]))
        hashes = {name: file_hash(self.dataset / (name + ".jsonl")) for name in ("train", "validation", "test")}
        self.readiness = self.root / "readiness.json"
        write_json(self.readiness, dict(schema="assembly-lifecycle-readiness.v1", all_fit=True,
            audit=dict(status="disjoint"), model_id=MODEL_ID, revision=REVISION,
            contract_path=str(self.contract), contract_sha256=file_hash(self.contract),
            dataset_report_sha256=file_hash(self.dataset / "report.json"), dataset_hashes=hashes,
            partitions=dict(train=dict(samples=32), validation=dict(samples=1), test=dict(samples=1)),
            context_length=1024, decision_tokens=256))
        self.manifest = dict(config=cfg, requested_max_steps=None, dataset_path=str(self.dataset / "train.jsonl"),
            partition_hashes=hashes, dataset_sha256=hashes["train"], samples=32, validation_samples=1,
            excluded_over_context=0, validation_excluded_over_context=0, context_length=1024,
            split_audit=dict(status="disjoint"), protocol_hashes=[cfg["protocol_hash"]], snapshot=cfg["snapshot"],
            optimizer_steps=2, actual_epochs=1, adapter_files={}, elapsed_seconds=1,
            baseline_validation_metrics=dict(loss=1), validation_metrics=dict(loss=0.5))
        write_json(self.run / "training-manifest.json", self.manifest)
        write_json(self.run / "attempt-context-1024.json", dict(status="training_completed_not_accepted",
            optimizer_steps=2, actual_epochs=1))

    def inspect(self, manifest=None):
        with patch("release_readiness.validate_adapter", return_value=manifest or self.manifest), patch("builtins.print"):
            return inspect_release(self.run, self.readiness, self.root / "release.json")

    def test_complete_epoch_is_ready_for_comparison_only(self):
        result = self.inspect()
        self.assertEqual(result["expected_optimizer_steps"], 2)
        self.assertFalse(result["quality_accepted"])
        self.assertIsNone(result["winner"])
        self.assertFalse(result["inference_verified"])

    def test_shortened_runs_rejected(self):
        for changed in (dict(optimizer_steps=1), dict(actual_epochs=0.5), dict(requested_max_steps=2)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.inspect(dict(self.manifest, **changed))

    def test_changed_dataset_or_contract_rejected(self):
        target = self.dataset / "test.jsonl"
        original = target.read_text(encoding="utf-8")
        target.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Dataset partition changed"):
            self.inspect()
        target.write_text(original, encoding="utf-8")
        write_json(self.contract, dict(changed=True))
        with self.assertRaisesRegex(ValueError, "Frozen contract changed"):
            self.inspect()

    def test_skipped_or_different_configuration_rejected(self):
        for changed in (dict(excluded_over_context=1), dict(samples=31), dict(context_length=512),
                        dict(config=dict(self.manifest["config"], lora_rank=8))):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.inspect(dict(self.manifest, **changed))


if __name__ == "__main__":
    unittest.main()
