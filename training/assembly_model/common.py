"""Pinned model cache and training-input provenance validation."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
REVISION = "2e1fd397ee46e1388853d2af2c993145b0f1098a"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_hash(path):
    result = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def configure_cache():
    os.environ["HF_HOME"] = str(ROOT / "cache/huggingface")
    os.environ["HF_HUB_CACHE"] = str(ROOT / "cache/huggingface/hub")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def config(path=None):
    value = read_json(path or ROOT / "config.json")
    if value.get("model_id") != MODEL_ID or value.get("revision") != REVISION:
        raise ValueError("Training requires the pinned Qwen repository and revision")
    return value


def load_samples(path, *, expected_snapshot=None, expected_protocol=None):
    """Only accept samples accompanied by content-bound successful execution evidence.

    The dataset builder owns evidence creation. A source tag alone never grants
    training eligibility. Evidence paths are resolved within the dataset root.
    """
    path = Path(path).resolve()
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        source, messages = row.get("source", {}), row.get("messages", [])
        required = ("model_id", "family", "module_hashes", "snapshot", "protocol_hash", "trajectory_sha256")
        if any(key not in source or not source[key] for key in required):
            raise ValueError(f"Sample {line_number}: missing provenance")
        if expected_snapshot and source["snapshot"] != expected_snapshot:
            raise ValueError(f"Sample {line_number}: corpus snapshot mismatch")
        if expected_protocol and source["protocol_hash"] != expected_protocol:
            raise ValueError(f"Sample {line_number}: protocol mismatch")
        if len(messages) != 2 or [m.get("role") for m in messages] != ["user", "assistant"]:
            raise ValueError(f"Sample {line_number}: expected actual decision input/output messages")
        payload, decision = (json.loads(m["content"]) for m in messages)
        if not isinstance(payload, dict) or not isinstance(decision, dict):
            raise ValueError(f"Sample {line_number}: message contents must be JSON objects")
        if set(decision) != {"tool", "arguments_json", "reason"}:
            raise ValueError(f"Sample {line_number}: not an executed tool decision")
        arguments = json.loads(decision["arguments_json"])
        if not isinstance(arguments, dict):
            raise ValueError(f"Sample {line_number}: malformed tool arguments")
        evidence_ref = row.get("evidence", {})
        relative = evidence_ref.get("path", "")
        evidence_path = (path.parent / relative).resolve()
        if not relative or not evidence_path.is_relative_to(path.parent):
            raise ValueError(f"Sample {line_number}: missing or escaping execution evidence")
        if file_hash(evidence_path) != evidence_ref.get("sha256"):
            raise ValueError(f"Sample {line_number}: evidence hash mismatch")
        evidence = read_json(evidence_path)
        for flag in ("execution_succeeded", "structure_passed", "source_review_approved"):
            if evidence.get(flag) is not True:
                raise ValueError(f"Sample {line_number}: {flag} is not verified")
        if evidence.get("trajectory_sha256") != source["trajectory_sha256"]:
            raise ValueError(f"Sample {line_number}: trajectory mismatch")
        if evidence.get("input_sha256") != sha256(messages[0]["content"].encode()).hexdigest():
            raise ValueError(f"Sample {line_number}: actual decision input mismatch")
        if evidence.get("decision_sha256") != sha256(messages[1]["content"].encode()).hexdigest():
            raise ValueError(f"Sample {line_number}: actual decision output mismatch")
        if decision["tool"] == "finish":
            if arguments or evidence.get("finish_verified") is not True:
                raise ValueError(f"Sample {line_number}: finish lacks verified export")
            completion = evidence.get("completion", {})
            if (completion.get("export_revision") != completion.get("revision")
                    or completion.get("export_revision") is None
                    or completion.get("archive_roundtrip_verified") is not True
                    or completion.get("planner_finished") is not True):
                raise ValueError(f"Sample {line_number}: finish lacks current-revision archive evidence")
        rows.append(row)
    if not rows:
        raise ValueError("No approved successful execution samples; training not started")
    return rows


def tokenize_samples(rows, tokenizer, context_length):
    encoded, skipped, _ = tokenize_sample_rows(rows, tokenizer, context_length)
    return encoded, skipped


def tokenize_sample_rows(rows, tokenizer, context_length):
    """Train assistant tokens only; never truncate away mission context silently."""
    result, skipped, included = [], 0, []
    for row in rows:
        messages = row["messages"]
        prefix = tokenizer.apply_chat_template(messages[:1], tokenize=True, add_generation_prompt=True)
        whole = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
        if len(whole) > context_length:
            skipped += 1
            continue
        if whole[:len(prefix)] != prefix:
            raise ValueError("Chat template prefix cannot be aligned for assistant-only loss")
        labels = [-100] * len(prefix) + whole[len(prefix):]
        if all(value == -100 for value in labels):
            raise ValueError("Sample has no assistant training tokens")
        result.append(dict(input_ids=whole, attention_mask=[1] * len(whole), labels=labels))
        included.append(row)
    if not result:
        raise ValueError(f"No complete samples fit context_length={context_length}; skipped={skipped}")
    return result, skipped, included
